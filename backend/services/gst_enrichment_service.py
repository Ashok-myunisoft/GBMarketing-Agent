"""Evidence-based GSTIN enrichment used by the lead enrichment pipeline.

This service deliberately uses regular expressions for GSTIN discovery.  The
LLM only decides whether one of the already discovered candidates is supported
by the supplied evidence; it can never introduce a new GSTIN.
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus, urlparse

from playwright.sync_api import BrowserContext, Page

from services.browser_service import BrowserService
from services.llm_services import LLMService
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.IGNORECASE)
GOOGLE_SEARCH_URL = "https://www.google.com/search?q="
MAX_RESULTS = 10
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
NAME_STOP_WORDS = {"private", "pvt", "limited", "ltd", "llp", "india", "the", "and", "of"}


@dataclass(frozen=True)
class GstEnrichmentResult:
    gst_number: Optional[str]
    confidence: int
    source: list[str]


@dataclass(frozen=True)
class GstCandidateEvidence:
    gst_number: str
    sources: list[str]
    snippets: list[str]
    score: int


class GstEnrichmentService:
    """Finds and verifies GSTINs using rendered Google-result pages."""

    def __init__(self, browser: BrowserService, llm: Optional[LLMService] = None):
        self._browser = browser
        self._llm = llm
        self._prompt_service = PromptService()

    def resolve(
        self,
        official_company_name: str,
        official_website: str,
        official_address: Optional[str] = None,
        known_pages: Optional[list[tuple[str, str]]] = None,
    ) -> GstEnrichmentResult:
        """Return a verified GSTIN, or ``null``-equivalent fields when unsure."""
        if not official_company_name or not official_website:
            return GstEnrichmentResult(None, 0, [])

        context: Optional[BrowserContext] = None
        try:
            # One context is shared across the Google search and all result
            # pages. This keeps cookies/session state and avoids creating a
            # browser context per result.
            context = self._browser.new_context()
            pages = list(known_pages or [])
            search_urls = self._google_result_urls(official_company_name, context)
            for url in search_urls[:MAX_RESULTS]:
                text = self._visible_text(url, context)
                if text:
                    pages.append((url, text))

            candidates = self._score_candidates(
                official_company_name, official_website, official_address, pages
            )
        except Exception as exc:
            logger.warning("GST evidence collection failed for %r: %s", official_company_name, exc)
            return GstEnrichmentResult(None, 0, [])
        finally:
            if context is not None:
                context.close()
        if not candidates:
            return GstEnrichmentResult(None, 0, [])

        preferred = candidates[0]
        verified = self._verify_with_llm(official_company_name, candidates)
        # Scoring, rather than a generative model, chooses the preferred value.
        # The verifier may only approve that value or reject all candidates.
        if verified != preferred.gst_number:
            return GstEnrichmentResult(None, 0, [])
        confidence = min(96, max(0, preferred.score))
        return GstEnrichmentResult(preferred.gst_number, confidence, preferred.sources)

    def _google_result_urls(self, company_name: str, context: BrowserContext) -> list[str]:
        queries = [
            f'"{company_name}" GST Number',
            f'"{company_name}" GSTIN',
            f'"{company_name}" GST',
            f'"{company_name}" GST Registration',
        ]
        urls: list[str] = []
        # The fallback queries are used only if the earlier query produced no
        # usable organic links, avoiding unnecessary browsing in the common case.
        for query in queries:
            found = self._google_search(query, context)
            for url in found:
                if url not in urls:
                    urls.append(url)
                if len(urls) >= MAX_RESULTS:
                    return urls
            if urls:
                break
        return urls

    def _google_search(self, query: str, context: BrowserContext) -> list[str]:
        page = self._browser.new_page(context)
        try:
            self._browser.goto(page, f"{GOOGLE_SEARCH_URL}{quote_plus(query)}")
            page.wait_for_timeout(1000)
            urls: list[str] = []
            # Google result titles are substantially less noisy than all anchors.
            for index in range(min(page.locator("h3").count(), MAX_RESULTS * 2)):
                h3 = page.locator("h3").nth(index)
                anchor = h3.locator("xpath=ancestor::a[1]")
                href = anchor.get_attribute("href") if anchor.count() else None
                if self._is_organic_url(href) and href not in urls:
                    urls.append(href)
                if len(urls) >= MAX_RESULTS:
                    break
            return urls
        except Exception as exc:
            logger.info("GST Google search failed for %r: %s", query, exc)
            return []
        finally:
            page.close()

    @staticmethod
    def _is_organic_url(url: Optional[str]) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        host = urlparse(url).netloc.lower()
        return bool(host and "google." not in host)

    def _visible_text(self, url: str, context: BrowserContext) -> str:
        page = self._browser.new_page(context)
        try:
            self._browser.goto(page, url)
            page.wait_for_timeout(750)
            return page.locator("body").inner_text(timeout=5000)
        except Exception as exc:
            logger.info("GST source page could not be read (%s): %s", url, exc)
            return ""
        finally:
            page.close()

    def _score_candidates(
        self,
        company_name: str,
        official_website: str,
        official_address: Optional[str],
        pages: list[tuple[str, str]],
    ) -> list[GstCandidateEvidence]:
        evidence: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for url, text in pages:
            for raw in GSTIN_PATTERN.findall(text or ""):
                gst = raw.upper()
                if self._is_valid_gstin(gst):
                    evidence[gst].append((url, self._snippet(text, gst)))

        result: list[GstCandidateEvidence] = []
        for gst, entries in evidence.items():
            unique_sources = list(dict.fromkeys(url for url, _ in entries))
            snippets = list(dict.fromkeys(snippet for _, snippet in entries if snippet))[:3]
            score = 10  # Regex format plus checksum validation.
            # Rules are evaluated against each complete rendered page, not a
            # search snippet: the legal name/address can be far from its GSTIN.
            page_text = "\n".join(
                text for url, text in pages if url in set(unique_sources)
            )
            if self._company_matches(company_name, page_text):
                score += 50
            if any(self._same_host(url, official_website) for url in unique_sources):
                score += 20
            if official_address and self._address_consistent(official_address, page_text):
                score += 15
            if len({self._host(url) for url in unique_sources}) > 1:
                score += 25
            result.append(GstCandidateEvidence(gst, unique_sources, snippets, score))
        return sorted(result, key=lambda item: (-item.score, item.gst_number))

    def _verify_with_llm(
        self, company_name: str, candidates: list[GstCandidateEvidence]
    ) -> Optional[str]:
        payload = [
            {"gst_number": item.gst_number, "score": item.score, "source_urls": item.sources,
             "page_snippets": item.snippets}
            for item in candidates
        ]
        try:
            if self._llm is None:
                self._llm = LLMService()
            response = self._llm.invoke(
                system_prompt=self._prompt_service.load("gst_enrichment"),
                user_prompt=json.dumps({"official_company_name": company_name, "candidates": payload}),
                temperature=0.0,
                max_tokens=120,
            )
            value = json.loads(response).get("gst_number")
            value = str(value).upper() if value else None
            allowed = {item.gst_number for item in candidates}
            return value if value in allowed else None
        except Exception as exc:
            logger.info("GST verification unavailable for %r: %s", company_name, exc)
            return None

    @staticmethod
    def _snippet(text: str, gst: str) -> str:
        position = text.upper().find(gst)
        if position < 0:
            return ""
        return re.sub(r"\s+", " ", text[max(0, position - 350):position + len(gst) + 350]).strip()

    @staticmethod
    def _company_matches(company_name: str, text: str) -> bool:
        terms = [term.lower() for term in re.findall(r"[A-Za-z0-9]+", company_name)
                 if len(term) > 2 and term.lower() not in NAME_STOP_WORDS]
        lowered = text.lower()
        return bool(terms) and all(term in lowered for term in terms)

    @staticmethod
    def _address_consistent(official_address: str, text: str) -> bool:
        tokens = set(re.findall(r"[a-z0-9]+", official_address.lower()))
        tokens -= {"india", "road", "street", "near", "the", "and", "for"}
        found = sum(token in text.lower() for token in tokens if len(token) > 2)
        return found >= 2

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    @classmethod
    def _same_host(cls, first: str, second: str) -> bool:
        return cls._host(first) == cls._host(second)

    @staticmethod
    def _is_valid_gstin(gst: str) -> bool:
        if not GSTIN_PATTERN.fullmatch(gst):
            return False
        total, factor = 0, 1
        for char in gst[:-1]:
            value = GSTIN_CHARSET.index(char) * factor
            total += value // 36 + value % 36
            factor = 2 if factor == 1 else 1
        return gst[-1] == GSTIN_CHARSET[(36 - total % 36) % 36]
