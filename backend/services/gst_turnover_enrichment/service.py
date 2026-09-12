"""Company-name AI web-search research for GST and turnover only.

OpenAI's Responses API (hosted web_search tool) is both the search AND
extraction mechanism - see openai_research_client.py. Neither SearXNG nor
Crawl4AI is used anywhere in this module; the search query strategy,
source preferences, and extraction rules all live in the prompt templates
(app/prompts/gst_search.md, app/prompts/turnover_search.md), never
hardcoded here.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from config.geography import gst_state_conflict
from core.config import settings
from services.browser_service import BrowserService
from services.gst_turnover_enrichment import ai_validator, confidence
from services.gst_turnover_enrichment.gst_extraction import find_valid_gstins
from services.gst_turnover_enrichment.models import FieldResult, GstTurnoverResult, SourceCandidate
from services.gst_turnover_enrichment.openai_research_client import AIResearchResult, OpenAIResearchClient

logger = logging.getLogger(__name__)
_LOG_TAG = {"GST Number": "GST", "Turnover": "TURNOVER"}


class GstTurnoverEnrichmentService:
    """Researches GST/turnover via OpenAI's Responses API + hosted web
    search - one self-contained research call per field, replacing the
    previous SearXNG-search + Crawl4AI-crawl pipeline entirely."""

    def __init__(
        self, browser: BrowserService,
        research: Optional[OpenAIResearchClient] = None,
    ):
        # ``browser`` remains an injected argument for compatibility with the
        # existing EnrichmentAgent construction; it is deliberately unused.
        self._browser = browser
        self._research = research or OpenAIResearchClient()

    def resolve(
        self, company_name: str, website: Optional[str] = None, gst: Optional[str] = None,
        city: Optional[str] = None, state: Optional[str] = None, industry: Optional[str] = None,
        official_name: Optional[str] = None, address: Optional[str] = None, cin: Optional[str] = None,
    ) -> GstTurnoverResult:
        """Resolve fields independently via OpenAI web-search research.

        The two fields run concurrently - each is a single, self-contained
        OpenAI Responses API call (its own real web search + reasoning),
        sharing no state - same reasoning the previous SearXNG/Crawl4AI
        calls relied on for safe concurrent use of a plain ThreadPoolExecutor.
        """
        company_fields = dict(
            company_name=company_name, official_name=official_name, website=website,
            city=city, state=state, industry=industry, address=address, cin=cin,
        )
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gst-turnover-field") as executor:
            gst_future = None if gst else executor.submit(self._resolve_gst, company_name, state, company_fields)
            turnover_future = executor.submit(
                self._resolve_turnover, company_name, state, company_fields
            ) if settings.ENRICHMENT_LOOKUP_TURNOVER else None

            gst_result = gst_future.result() if gst_future else FieldResult(status="not_found")
            turnover_result = turnover_future.result() if turnover_future else FieldResult(status="not_found")

        return GstTurnoverResult(gst=gst_result, turnover=turnover_result)

    def _resolve_gst(self, company_name: str, state: Optional[str], company_fields: dict) -> FieldResult:
        logger.info("[%s] ai_research_query=\"%s\" GST number", _LOG_TAG["GST Number"], company_name)
        research = self._research.search_gst(**company_fields)
        candidates = self._gst_candidates(research)
        return self._resolve_field("GST Number", company_name, candidates, confidence.GST_SOURCE_POINTS, state=state)

    def _resolve_turnover(self, company_name: str, state: Optional[str], company_fields: dict) -> FieldResult:
        logger.info("[%s] ai_research_query=\"%s\" turnover", _LOG_TAG["Turnover"], company_name)
        research = self._research.search_turnover(**company_fields)
        candidates = self._turnover_candidates(research)
        return self._resolve_field("Turnover", company_name, candidates, confidence.TURNOVER_SOURCE_POINTS, state=state)

    @staticmethod
    def _gst_candidates(research: AIResearchResult) -> "list[SourceCandidate]":
        """Never trusts the model's returned GSTIN string on its own - runs
        it through the exact same checksum validator every other GST tier
        in this app uses (find_valid_gstins), so a hallucinated or
        malformed value can never reach the rest of the pipeline. One
        candidate per source URL the model cited, all sharing the same
        value, lets the existing confidence scoring reward genuine
        multi-domain corroboration exactly as it already does for every
        other source type."""
        if research.status != "found" or not research.value:
            return []
        valid = find_valid_gstins(research.value)
        if not valid:
            logger.warning(
                "[GST] ai_research returned a value that failed checksum validation: %s", research.value,
            )
            return []
        gstin = valid[0]
        evidence = _with_confidence_label(research.evidence, research.confidence_label)
        sources = research.sources or [""]
        return [
            SourceCandidate(
                value=gstin, source="ai_web_search", source_url=url,
                source_type="ai_web_search", evidence=evidence,
            )
            for url in sources
        ]

    @staticmethod
    def _turnover_candidates(research: AIResearchResult) -> "list[SourceCandidate]":
        if research.status != "found" or not research.value:
            return []
        evidence = _with_confidence_label(research.evidence, research.confidence_label)
        sources = research.sources or [""]
        return [
            SourceCandidate(
                value=research.value, source="ai_web_search", source_url=url,
                currency=research.currency, financial_year=research.financial_year, metric=research.metric,
                source_type="ai_web_search", evidence=evidence,
            )
            for url in sources
        ]

    @staticmethod
    def _resolve_field(
        field_name: str, company_name: str, candidates: "list[SourceCandidate]", points_table: dict,
        state: Optional[str] = None,
    ) -> FieldResult:
        tag = _LOG_TAG[field_name]
        if not candidates:
            return FieldResult(status="not_found")
        scored = confidence.score_candidates(candidates, points_table)
        sources_by_value: dict[str, set] = {}
        evidence_by_value: dict[str, str] = {}
        for candidate in candidates:
            sources_by_value.setdefault(candidate.value, set()).add(candidate.source)
            # First non-empty snippet seen for this value wins - later
            # candidates for the same value rarely add a materially
            # different context, and this keeps the prompt short.
            if candidate.evidence and not evidence_by_value.get(candidate.value):
                evidence_by_value[candidate.value] = candidate.evidence
        ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)

        # A GST candidate whose registered state contradicts the company's
        # own known state is demoted (never dropped) below every candidate
        # without that conflict, at equal-or-lower confidence - a cheap,
        # deterministic pre-check that catches the common case (a
        # supplier/customer's GSTIN on the same page) before spending an
        # LLM call on it. The AI validator below still sees every candidate,
        # including a demoted one, if nothing else exists.
        if field_name == "GST Number" and state:
            ranked = sorted(
                ranked,
                key=lambda item: (gst_state_conflict(item[0], state) is None, item[1]),
                reverse=True,
            )

        ranked_values = [
            (value, score, sorted(sources_by_value[value]), evidence_by_value.get(value, ""))
            for value, score in ranked
        ]
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


def _with_confidence_label(evidence: str, confidence_label: "Optional[str]") -> str:
    if not confidence_label:
        return evidence
    suffix = f"(model confidence: {confidence_label})"
    return f"{evidence} {suffix}" if evidence else suffix
