"""
Step 3: AI validation. The model never searches the internet and never
invents a value - it only compares candidates already found deterministically
by the website (Firecrawl) and jamku turnover tiers and picks the one with
the strongest supporting evidence. Mirrors the existing contact-disambiguation pattern
(services/contact_extraction/llm_disambiguator.py): gated by a settings
flag, degrades to the top-scored deterministic candidate on any failure,
timeout, or out-of-range answer, and can only select an index into the list
it was given.
"""

import json
import logging

from core.config import settings
from services.llm_services import LLMService
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)

# (value, confidence, sources)
RankedCandidate = "tuple[str, int, list[str]]"


def validate(field_name: str, company_name: str, ranked_values: "list[RankedCandidate]") -> "str | None":
    """`ranked_values` must already be sorted highest-confidence first.
    Returns the AI-selected value, or None to keep using the top-ranked
    deterministic value as-is (the caller's existing fallback)."""

    if not settings.ENRICHMENT_GST_TURNOVER_AI_VALIDATION or len(ranked_values) < 2:
        return None

    try:
        system_prompt = PromptService.load("gst_turnover_validation")
        response = LLMService().invoke(
            system_prompt=system_prompt,
            user_prompt=_build_user_prompt(field_name, company_name, ranked_values),
            temperature=0.0,
            max_tokens=200,
        )
        payload = _parse_json(response)
    except Exception as ex:
        logger.warning("%s AI validation failed for '%s': %s", field_name, company_name, ex)
        return None

    index = payload.get("selected_index") if isinstance(payload, dict) else None
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(ranked_values):
        return None

    return ranked_values[index][0]


def _build_user_prompt(field_name: str, company_name: str, ranked_values: "list[RankedCandidate]") -> str:
    lines = [f"Company: {company_name}", f"Field: {field_name}", "Candidates:"]
    for index, (value, confidence, sources) in enumerate(ranked_values):
        lines.append(f"{index}. Value: {value} | Confidence: {confidence} | Sources: {', '.join(sources)}")
    return "\n".join(lines)


def _parse_json(response: str) -> dict:
    cleaned = response.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)
