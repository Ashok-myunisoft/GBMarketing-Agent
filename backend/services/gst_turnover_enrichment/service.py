"""Company-name Firecrawl Search enrichment for GST and turnover only."""

import logging
import re
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from core.config import settings
from services.browser_service import BrowserService
from services.gst_turnover_enrichment import ai_validator, confidence
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
    """Searches Firecrawl using a company name; it never scrapes its website."""

    def __init__(self, browser: BrowserService, firecrawl: FirecrawlClient):
        # ``browser`` remains an injected argument for compatibility with the
        # existing EnrichmentAgent construction; it is deliberately unused.
        self._browser = browser
        self._firecrawl = firecrawl

    def resolve(
        self, company_name: str, website: Optional[str] = None, gst: Optional[str] = None,
        city: Optional[str] = None, state: Optional[str] = None, industry: Optional[str] = None,
    ) -> GstTurnoverResult:
        """Resolve fields independently from Firecrawl Search result content.

        ``website`` is accepted solely to preserve the existing call contract.
        It is never read or supplied to Firecrawl in this GST/turnover path.
        """
        del website
        gst_candidates = self._search_field(company_name, "gst", city, state, industry) if not gst else []
        turnover_candidates = (
            self._search_field(company_name, "turnover", city, state, industry)
            if settings.ENRICHMENT_LOOKUP_TURNOVER else []
        )
        gst_result = self._resolve_field("GST Number", company_name, gst_candidates, confidence.GST_SOURCE_POINTS)
        turnover_result = self._resolve_field("Turnover", company_name, turnover_candidates, confidence.TURNOVER_SOURCE_POINTS)
        return GstTurnoverResult(gst=gst_result, turnover=turnover_result)

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
            return FieldResult(confidence=score, source_url=selected.source_url, status="unverified", source_type=selected.source_type)
        logger.info("[%s] found company=%s value=%s source=%s", tag, company_name, value, selected.source_url)
        return FieldResult(value=value, confidence=score, sources=sorted(sources_by_value[value]), source_url=selected.source_url,
                           financial_year=selected.financial_year, metric=selected.metric, currency=selected.currency,
                           status="verified", source_type=selected.source_type)
