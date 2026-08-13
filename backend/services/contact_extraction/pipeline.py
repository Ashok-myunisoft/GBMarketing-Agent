"""
ContactExtractionPipeline: the single entry point `enrichment_agent.py` calls
into. Wires page extraction -> external-candidate wrapping -> dedupe/merge
(Step 7/8) -> score (Step 6) -> optional LLM tie-break -> final ranked result
(Step 9), so the calling agent never has to know how any of that works -
just three scalar fields back out, matching the existing `Company` contract.
"""

import logging
from typing import Optional

from core.config import settings
from services.contact_extraction import dedupe, dom_extractor, llm_disambiguator, scorer
from services.contact_extraction.designation_rules import canonical_designation
from services.contact_extraction.models import ContactCandidate, ContactExtractionResult, PageCategory
from services.contact_extraction.name_rules import is_person_name, normalize_name

logger = logging.getLogger(__name__)

# Candidates within this many points of the top score are treated as a
# genuine tie - only consulted when the LLM fallback is enabled at all.
TIE_MARGIN = 10.0


class ContactExtractionPipeline:
    """One instance per company being enriched; not thread-shared."""

    def __init__(self, company_name: str, company_website: Optional[str] = None):
        self._company_name = company_name
        self._company_website = company_website

    def extract_from_page(self, page, source_url: str, page_category: PageCategory) -> "list[ContactCandidate]":
        """DOM container strategy, then a text-block fallback if that finds nothing (Steps 2/3)."""
        try:
            return dom_extractor.extract_candidates(page, source_url, page_category)
        except Exception as ex:
            logger.warning("Contact candidate extraction failed for %s: %s", source_url, ex)
            return []

    def external_candidate(
        self,
        name: Optional[str],
        title: Optional[str],
        source: str,
        source_url: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        linkedin_url: Optional[str] = None,
    ) -> Optional[ContactCandidate]:
        """Wraps a FileSure/LinkedIn hit as a candidate for the same scoring/dedupe pass (Step 7)."""

        if not name or not is_person_name(name):
            return None

        designation = canonical_designation(title) if title else None
        evidence = [f'Found via {source} as "{title}"'] if title else [f"Found via {source}"]

        return ContactCandidate(
            name=normalize_name(name),
            raw_title=title or "",
            canonical_designation=designation,
            page_category=PageCategory.OTHER,
            source_url=source_url or source,
            email=email,
            phone=phone,
            linkedin_url=linkedin_url,
            source=source,
            evidence=evidence,
        )

    def select_best(self, candidates: "list[ContactCandidate]") -> Optional[ContactExtractionResult]:
        """dedupe -> score -> optional LLM tie-break -> top candidate, or None below the confidence floor (Step 9)."""

        candidates = [candidate for candidate in candidates if candidate]
        if not candidates:
            return None

        merged = dedupe.merge(candidates)
        for candidate in merged:
            scorer.score(candidate, self._company_website)
        merged.sort(key=lambda candidate: candidate.score, reverse=True)

        top = merged[0]
        if settings.ENRICHMENT_LOOKUP_LLM_CONTACT and len(merged) > 1:
            tied = [candidate for candidate in merged if top.score - candidate.score <= TIE_MARGIN]
            if len(tied) > 1:
                chosen = llm_disambiguator.disambiguate(tied, self._company_name)
                if chosen is not None:
                    top = chosen

        if top.score < settings.ENRICHMENT_CONTACT_MIN_CONFIDENCE:
            return None

        return ContactExtractionResult(
            contact_person=top.name,
            designation=top.canonical_designation,
            linkedin_url=top.linkedin_url,
            confidence=top.score,
            evidence=top.evidence,
            source_urls=sorted(top.source_urls),
        )
