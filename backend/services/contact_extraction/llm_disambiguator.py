"""
Optional LLM tie-break (Step 4's "fallback to an LLM only when traditional
extraction cannot confidently identify a name", and Step 10's "minimize LLM
calls, deterministic first").

Off by default via `settings.ENRICHMENT_LOOKUP_LLM_CONTACT`, mirroring the
existing `ENRICHMENT_LOOKUP_LINKEDIN` opt-in pattern - this never runs unless
explicitly enabled. The model is only ever asked to pick an index from
candidates already found deterministically, never to invent a name, so a
malformed or out-of-range response can only be discarded, never fabricate a
contact.
"""

import json
import logging

from core.config import settings
from services.contact_extraction.models import ContactCandidate
from services.llm_services import LLMService
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)


def disambiguate(candidates: "list[ContactCandidate]", company_name: str) -> "ContactCandidate | None":
    """Asks the LLM to pick the correct contact among tied candidates.

    Returns None on any failure, disagreement, or an out-of-range/declined
    answer - callers keep using the top deterministically-scored candidate
    in that case. Never raises.
    """

    if not settings.ENRICHMENT_LOOKUP_LLM_CONTACT or len(candidates) < 2:
        return None

    try:
        system_prompt = PromptService.load("contact_disambiguation")
        response = LLMService().invoke(
            system_prompt=system_prompt,
            user_prompt=_build_user_prompt(candidates, company_name),
            temperature=0.0,
            max_tokens=200,
        )
        payload = _parse_json(response)
    except Exception as ex:
        logger.warning("Contact disambiguation LLM call failed for '%s': %s", company_name, ex)
        return None

    index = payload.get("selected_index") if isinstance(payload, dict) else None
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(candidates):
        return None

    return candidates[index]


def _build_user_prompt(candidates: "list[ContactCandidate]", company_name: str) -> str:
    lines = [f"Company: {company_name}", "Candidates:"]
    for index, candidate in enumerate(candidates):
        lines.append(
            f"{index}. Name: {candidate.name} | Title: {candidate.raw_title} | "
            f"Designation: {candidate.canonical_designation} | Page: {candidate.source_url}"
        )
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
