"""Company-name Firecrawl Search enrichment for GST and turnover only."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from core.config import settings
from services.browser_service import BrowserService
from services.gst_turnover_enrichment import ai_validator, confidence
from services.gst_turnover_enrichment.crawl4ai_client import Crawl4AIClient
from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient, FirecrawlSearchResult
from services.gst_turnover_enrichment.gst_extraction import find_valid_gstins
from services.gst_turnover_enrichment.models import FieldResult, GstTurnoverResult, SourceCandidate
from services.gst_turnover_enrichment.turnover_extraction import find_turnover_candidates

logger = logging.getLogger(__name__)
_LOG_TAG = {"GST Number": "GST", "Turnover": "TURNOVER"}


def _normalize_result_url(url: str) -> str:
    """Normalize a search-result URL for deduplication: lowercase
    scheme/host, drop the fragment, and strip a trailing slash so
    ``https://Example.com/x/`` and ``https://example.com/x#y`` dedupe
    together."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


class GstTurnoverEnrichmentService:
    """Searches Firecrawl by company name with Crawl4AI as a page-level fallback."""

    def __init__(self, browser: BrowserService, firecrawl: FirecrawlClient, crawl4ai: Optional[Crawl4AIClient] = None):
        # ``browser`` remains an injected argument for compatibility with the
        # existing EnrichmentAgent construction; it is deliberately unused.
        self._browser = browser
        self._firecrawl = firecrawl
        # Fallback only - see _crawl4ai_fallback. Never used while Firecrawl
        # Search already resolved a field.
        self._crawl4ai = crawl4ai or Crawl4AIClient()

    def resolve(
        self, company_name: str, website: Optional[str] = None, gst: Optional[str] = None,
        city: Optional[str] = None, state: Optional[str] = None, industry: Optional[str] = None,
    ) -> GstTurnoverResult:
        """Resolve fields independently from Firecrawl Search result content.

        Firecrawl is always tried first. ``website``, previously unused here,
        is now read only as the Crawl4AI fallback target for whichever field
        (GST, turnover, or both) Firecrawl could not resolve.

        The two fields' full search->resolve->fallback chains run
        concurrently rather than one after the other: both go through
        FirecrawlClient's plain HTTP calls (thread-safe: its own rate
        limiter/semaphore/cache all use locks) and, on fallback,
        Crawl4AIClient (a fresh self-contained browser per call, never a
        shared one) - unlike EnrichmentAgent's own Playwright browser, there
        is no single shared browser instance here for two threads to
        conflict over. Both fields still ultimately queue behind the same
        global Firecrawl rate limit, but no longer wait for each other's
        non-network work (ranking, scoring, the AI validation call) in
        between - this alone doesn't reduce the number of Firecrawl calls a
        "not found" company costs, only the wall-clock time to make them.
        """
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gst-turnover-field") as executor:
            gst_future = None if gst else executor.submit(
                self._resolve_one_field, "GST Number", "gst", company_name, website, city, state, industry
            )
            turnover_future = executor.submit(
                self._resolve_one_field, "Turnover", "turnover", company_name, website, city, state, industry
            ) if settings.ENRICHMENT_LOOKUP_TURNOVER else None

            gst_result = gst_future.result() if gst_future else FieldResult(status="not_found")
            turnover_result = turnover_future.result() if turnover_future else FieldResult(status="not_found")

        return GstTurnoverResult(gst=gst_result, turnover=turnover_result)

    def _resolve_one_field(
        self, field_name: str, field: str, company_name: str, website: Optional[str],
        city: Optional[str], state: Optional[str], industry: Optional[str],
    ) -> FieldResult:
        """One field's complete search -> resolve -> Crawl4AI-fallback chain.

        Only ever called (via resolve()) for a field that actually needs
        resolving - the "already known"/"turnover lookup disabled" skips
        stay in resolve() itself, not here.
        """
        points_table = confidence.GST_SOURCE_POINTS if field == "gst" else confidence.TURNOVER_SOURCE_POINTS
        candidates = self._search_field(company_name, field, city, state, industry)
        result = self._resolve_field(field_name, company_name, candidates, points_table)
        if result.status == "not_found":
            result = self._crawl4ai_fallback(
                field_name, field, company_name, website, city, state, industry, points_table
            ) or result
        return result

    def _crawl4ai_fallback(
        self, field_name: str, field: str, company_name: str, website: Optional[str],
        city: Optional[str], state: Optional[str], industry: Optional[str], points_table: dict,
    ) -> Optional[FieldResult]:
        """Runs only when Firecrawl Search could not resolve the field.

        Crawl4AI is used only against real candidate web pages. It never
        crawls a search-engine HTML endpoint, so DuckDuckGo/Google anti-bot
        pages cannot break the fallback.

        Fallback order:
          1. Crawl the known company website, when available.
          2. If no value was found there, ask Firecrawl Search for candidate
             result URLs and crawl those URLs directly with Crawl4AI.
          3. Return None when no usable evidence is found.

        The existing Firecrawl Search -> extraction -> validation flow remains
        unchanged; this method is only a fallback."""
        if not settings.CRAWL4AI_ENABLED:
            return None

        tag = _LOG_TAG[field_name]
        crawled_urls: set[str] = set()

        # Tier 1: known company website.
        if website:
            normalized_website = _normalize_result_url(website)
            crawled_urls.add(normalized_website)

            candidates = self._crawl4ai_extract(
                company_name,
                field,
                website,
                "website",
                city,
                state,
                industry,
            )
            if candidates:
                result = self._resolve_field(
                    field_name, company_name, candidates, points_table
                )
                logger.info(
                    "[%s] provider=crawl4ai tier=website company=%s url=%s status=%s",
                    tag,
                    company_name,
                    website,
                    result.status,
                )
                return result

        # Tier 2: use Firecrawl Search only to discover real result URLs.
        # Crawl4AI then opens those URLs directly instead of crawling
        # html.duckduckgo.com, which was returning HTTP 403 anti-bot errors.
        crawl_budget = max(0, settings.MAX_RESULT_URLS_PER_FIELD)

        if crawl_budget:
            for query in self._queries(company_name, field):
                try:
                    search_results = self._firecrawl.search(
                        query,
                        limit=10,
                        company=company_name,
                    )
                except Exception:
                    logger.exception(
                        "[%s] provider=firecrawl tier=fallback_search company=%s query=%s",
                        tag,
                        company_name,
                        query,
                    )
                    continue

                ranked_results = self._rank_results(
                    company_name,
                    field,
                    search_results,
                    city,
                    state,
                    industry,
                )

                for search_result in ranked_results:
                    if crawl_budget <= 0:
                        break

                    url = (search_result.url or "").strip()
                    if not url:
                        continue

                    normalized_url = _normalize_result_url(url)
                    if normalized_url in crawled_urls:
                        continue

                    # Never crawl search-engine result pages. Only crawl
                    # actual HTTP(S) web pages returned by Firecrawl.
                    parts = urlsplit(url)
                    if parts.scheme.lower() not in {"http", "https"}:
                        continue
                    if "duckduckgo.com" in parts.netloc.lower():
                        continue
                    if "google." in parts.netloc.lower():
                        continue
                    if "bing.com" in parts.netloc.lower():
                        continue

                    crawled_urls.add(normalized_url)
                    crawl_budget -= 1

                    candidates = self._crawl4ai_extract(
                        company_name,
                        field,
                        url,
                        "search",
                        city,
                        state,
                        industry,
                    )
                    if not candidates:
                        continue

                    result = self._resolve_field(
                        field_name,
                        company_name,
                        candidates,
                        points_table,
                    )
                    logger.info(
                        "[%s] provider=crawl4ai tier=firecrawl_result_url "
                        "company=%s url=%s status=%s",
                        tag,
                        company_name,
                        url,
                        result.status,
                    )
                    return result

        logger.info(
            "[%s] provider=crawl4ai company=%s status=not_found",
            tag,
            company_name,
        )
        return None

    def _crawl4ai_extract(
        self, company_name: str, field: str, url: str, source: str,
        city: Optional[str], state: Optional[str], industry: Optional[str],
    ) -> list[SourceCandidate]:
        """Crawls one URL with Crawl4AI and runs it through the same
        extraction functions the Firecrawl path uses. ``source`` records
        which fallback tier produced the evidence ("website" or "search")
        so confidence scoring weighs it the same way the Firecrawl path
        already weighs those tiers."""
        page = self._crawl4ai.crawl(url)
        if not page.success or not page.content:
            return []
        if not self._matches_identity(company_name, page.content, city, state, industry):
            return []

        if field == "gst":
            return [
                SourceCandidate(value=value, source=source, source_url=url, source_type="crawl4ai_fallback")
                for value in find_valid_gstins(page.content)
            ]
        return [
            SourceCandidate(value=item["value"], source=source, source_url=url, currency=item["currency"],
                             financial_year=item["financial_year"], metric=item["metric"], source_type="crawl4ai_fallback")
            for item in find_turnover_candidates(page.content)
        ]

    def _search_field(
        self, company_name: str, field: str, city: Optional[str], state: Optional[str], industry: Optional[str]
    ) -> list[SourceCandidate]:
        for query in self._queries(company_name, field):
            search_results = self._firecrawl.search(query, limit=10, company=company_name)
            results = self._rank_results(company_name, field, search_results, city, state, industry)
            candidates = self._extract_candidates(company_name, field, results, city, state, industry)
            if candidates:
                return candidates
        logger.info("[%s] not_found company=%s", _LOG_TAG[field == "gst" and "GST Number" or "Turnover"], company_name)
        return []

    @staticmethod
    def _queries(company_name: str, field: str) -> list[str]:
        name = f'"{company_name.strip()}"'
        queries = ([f"{name} GST", f"{name} GSTIN", f'{name} "GST number"'] if field == "gst" else
                   [f"{name} turnover", f"{name} revenue", f'{name} "annual turnover"'])
        return queries[:min(3, settings.MAX_SEARCH_QUERIES_PER_FIELD)]

    def _extract_candidates(
        self, company_name: str, field: str, results: list[FirecrawlSearchResult],
        city: Optional[str], state: Optional[str], industry: Optional[str],
    ) -> list[SourceCandidate]:
        candidates: list[SourceCandidate] = []
        # Firecrawl Cloud Search returns title/description only, not page
        # content. When those alone don't establish a value, scrape the
        # result URL (existing self-hosted Firecrawl mechanism) for more
        # evidence - bounded so a single field never scrapes every result.
        scrape_budget = settings.MAX_RESULT_URLS_PER_FIELD
        for result in results:
            evidence = "\n".join((result.title, result.markdown, result.description)).strip()
            if not evidence or not self._matches_identity(company_name, evidence, city, state, industry):
                continue
            gstins = find_valid_gstins(evidence) if field == "gst" else []
            turnovers = find_turnover_candidates(evidence) if field != "gst" else []
            if not gstins and not turnovers and not result.markdown and scrape_budget > 0:
                scrape_budget -= 1
                page = self._firecrawl.scrape(result.url)
                if page.success and page.markdown:
                    scraped_evidence = "\n".join((result.title, page.markdown, result.description)).strip()
                    if self._matches_identity(company_name, scraped_evidence, city, state, industry):
                        evidence = scraped_evidence
                        gstins = find_valid_gstins(evidence) if field == "gst" else []
                        turnovers = find_turnover_candidates(evidence) if field != "gst" else []
            if field == "gst":
                candidates.extend(SourceCandidate(value=value, source="search", source_url=result.url, source_type="search_result")
                                  for value in gstins)
            else:
                candidates.extend(SourceCandidate(value=item["value"], source="search", source_url=result.url,
                                  currency=item["currency"], financial_year=item["financial_year"], metric=item["metric"],
                                  source_type="search_result") for item in turnovers)
        return candidates

    @classmethod
    def _rank_results(
        cls, company_name: str, field: str, results: list[FirecrawlSearchResult],
        city: Optional[str], state: Optional[str], industry: Optional[str],
    ) -> list[FirecrawlSearchResult]:
        keyword = "gst|gstin" if field == "gst" else "turnover|revenue"
        deduped = {_normalize_result_url(result.url): result for result in results if result.url}
        return sorted(deduped.values(), key=lambda result: (
            cls._matches_identity(company_name, f"{result.title} {result.markdown} {result.description}", city, state, industry),
            bool(re.search(keyword, f"{result.title} {result.markdown} {result.description}", re.I)),
            ".gov.in" in result.url or ".nic.in" in result.url,
            bool(result.markdown),
            -(result.position or 999),
        ), reverse=True)

    @staticmethod
    def _matches_identity(company_name: str, evidence: str, city: Optional[str], state: Optional[str], industry: Optional[str]) -> bool:
        normalize = lambda text: re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
        target, text = normalize(company_name), normalize(evidence)
        if not target or target not in text:
            ignored = {"pvt", "private", "ltd", "limited", "llp", "the", "and", "co"}
            tokens = [token for token in target.split() if len(token) > 2 and token not in ignored]
            if len(tokens) < 2 or sum(token in text for token in tokens) < 2:
                return False
        # Location/business matches strengthen identity when this data exists;
        # absence in a search result is not treated as a mismatch.
        for value in (city, state, industry):
            normalized = normalize(value)
            if normalized and normalized in text:
                return True
        return True

    @staticmethod
    def _resolve_field(field_name: str, company_name: str, candidates: list[SourceCandidate], points_table: dict) -> FieldResult:
        tag = _LOG_TAG[field_name]
        if not candidates:
            return FieldResult(status="not_found")
        scored = confidence.score_candidates(candidates, points_table)
        sources_by_value: dict[str, set] = {}
        for candidate in candidates:
            sources_by_value.setdefault(candidate.value, set()).add(candidate.source)
        ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
        ranked_values = [(value, score, sorted(sources_by_value[value])) for value, score in ranked]
        value = ai_validator.validate(field_name, company_name, ranked_values) or ranked_values[0][0]
        selected = next(candidate for candidate in candidates if candidate.value == value)
        score = scored[value]
        if score < 70:
            # Keep the extracted value even when confidence is below the
            # verification threshold. "unverified" means the value was found
            # but could not be verified strongly enough; it must not be
            # discarded because downstream Excel/export logic uses `value`.
            logger.info(
                "[%s] found_unverified company=%s value=%s confidence=%s source=%s",
                tag,
                company_name,
                value,
                score,
                selected.source_url,
            )
            return FieldResult(
                value=value,
                confidence=score,
                sources=sorted(sources_by_value[value]),
                source_url=selected.source_url,
                financial_year=selected.financial_year,
                metric=selected.metric,
                currency=selected.currency,
                status="unverified",
                source_type=selected.source_type,
            )
        logger.info("[%s] found company=%s value=%s source=%s", tag, company_name, value, selected.source_url)
        return FieldResult(value=value, confidence=score, sources=sorted(sources_by_value[value]), source_url=selected.source_url,
                           financial_year=selected.financial_year, metric=selected.metric, currency=selected.currency,
                           status="verified", source_type=selected.source_type)
