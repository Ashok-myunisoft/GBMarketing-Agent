"""
One LLM call that extracts a structured company record from the unified
document (services/crawler/document_builder.py), through the same
LLMService/PromptService plumbing every other LLM call in this app already
uses (services/gst_turnover_enrichment/ai_validator.py,
services/contact_extraction/llm_disambiguator.py).

Never raises: any failure (LLM unavailable, malformed/non-JSON response,
schema mismatch) degrades to an empty ExtractedCompanyRecord, so a bad LLM
response means "the Tavily pipeline found nothing this round" to callers,
never a crash.

Grounding check (see _ground): confirmed by hand against a real run (the
model was given a document containing zero occurrences of any designation
word, and still confidently returned "Managing Director" as the contact)
that the prompt's "never guess" instruction alone is not reliable enough for
the model behind this app's LLMService. Every factual field is therefore
independently verified to actually appear in the source document before
being trusted - the prompt is a request, this check is the actual guarantee.
"""

import json
import logging
import re
from typing import Any, Optional

from services.extractor.schema import ExtractedCompanyRecord
from services.llm_services import LLMService
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)

_FIELDS = (
    "company_name", "website", "contact_person",
    "designation", "email", "mobile", "address", "city", "state", "country",
    "pincode", "industry", "business_category",
)

# Concrete, checkable facts: a real value must appear in the document
# somewhere, or the model invented it. Excludes softer/interpretive fields
# (industry, business_category, city, state, country) where a reasonable
# inference from context - not a literal quote - is legitimate and expected.
_GROUNDED_FIELDS = (
    "company_name", "website", "contact_person",
    "designation", "email", "mobile", "address", "pincode",
)


def extract(document: str, company_name: str) -> ExtractedCompanyRecord:
    if not document or not document.strip():
        return ExtractedCompanyRecord()

    try:
        system_prompt = PromptService.load("company_extraction")
        response = LLMService().invoke(
            system_prompt=system_prompt,
            user_prompt=_build_user_prompt(company_name, document),
            temperature=0.0,
            max_tokens=1500,
        )
        payload = _parse_json(response)
    except Exception as ex:
        logger.warning("LLM structured extraction failed for '%s': %s", company_name, ex)
        return ExtractedCompanyRecord()

    if not isinstance(payload, dict):
        return ExtractedCompanyRecord()

    try:
        record = ExtractedCompanyRecord(
            **{key: _str_or_none(payload.get(key)) for key in _FIELDS},
            confidence=_int_dict(payload.get("confidence")),
            evidence=_str_dict(payload.get("evidence")),
            source_url=_str_dict(payload.get("source_url")),
        )
    except Exception as ex:
        logger.warning("LLM extraction response failed schema validation for '%s': %s", company_name, ex)
        return ExtractedCompanyRecord()

    return _ground(record, document, company_name)


def _ground(record: ExtractedCompanyRecord, document: str, company_name: str) -> ExtractedCompanyRecord:
    """Discards any _GROUNDED_FIELDS value that doesn't actually appear
    (itself, or its claimed evidence) in the source document - the model
    fabricating a plausible-looking value is treated exactly like it
    returning null, since neither is trustworthy without this check."""

    haystack = _normalize(document)
    grounded = record.model_copy(deep=True)

    for field_name in _GROUNDED_FIELDS:
        value = getattr(record, field_name)
        if value is None:
            continue

        evidence = record.evidence.get(field_name)
        found = _normalize(value) in haystack or (evidence and _normalize(evidence) in haystack)
        if not found:
            logger.warning(
                "Discarding ungrounded LLM field '%s'=%r for '%s' - not found in the source document",
                field_name, value, company_name,
            )
            setattr(grounded, field_name, None)
            grounded.confidence.pop(field_name, None)
            grounded.evidence.pop(field_name, None)
            grounded.source_url.pop(field_name, None)

    return grounded


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _build_user_prompt(company_name: str, document: str) -> str:
    return f"Company: {company_name}\n\nDocument:\n{document}"


def _parse_json(response: str) -> Any:
    cleaned = (response or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # The LLM call is capped at max_tokens (see extract() above) and can
        # get cut off mid-response - most often mid-string-value or with a
        # trailing key left incomplete. Rather than discarding every field
        # the model already returned, try to salvage the JSON up to its last
        # fully-formed element before giving up. Still raises (unchanged
        # behaviour) if the text isn't recoverable this way.
        repaired = _repair_truncated_json(cleaned)
        if repaired is None:
            raise
        return json.loads(repaired)


def _repair_truncated_json(text: str) -> Optional[str]:
    """Best-effort recovery for a JSON response cut off mid-structure.

    Walks `text` tracking bracket/string state and remembers the last point
    at which everything parsed so far was a complete, balanced element (right
    after a closing '}'/']' or at a top-level comma) along with what was
    still open at that point. If the text is truncated - meaning something
    is still open at the very end - it's cut back to that last safe point
    and the still-open brackets from there are appended to close it off.

    Deliberately conservative: returns None (no guess) whenever the JSON
    isn't simply truncated (already balanced, or malformed some other way),
    so the caller's existing json.JSONDecodeError handling is unaffected in
    every other case.
    """

    stack: "list[str]" = []
    in_string = False
    escape = False
    last_safe_index: Optional[int] = None
    last_safe_stack: Optional["list[str]"] = None

    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack or stack[-1] != char:
                return None  # Mismatched closer - not a simple truncation.
            stack.pop()
            last_safe_index = index + 1
            last_safe_stack = list(stack)
        elif char == "," and stack:
            last_safe_index = index
            last_safe_stack = list(stack)

    if not stack or last_safe_index is None or last_safe_stack is None:
        # Nothing left open (a different parse error, not truncation) or no
        # complete element was ever found to salvage.
        return None

    truncated = text[:last_safe_index].rstrip().rstrip(",")
    closers = "".join(reversed(last_safe_stack))
    return truncated + closers


def _str_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _int_dict(value: Any) -> "dict[str, int]":
    if not isinstance(value, dict):
        return {}
    result: "dict[str, int]" = {}
    for key, raw in value.items():
        try:
            result[str(key)] = max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            continue
    return result


def _str_dict(value: Any) -> "dict[str, str]":
    if not isinstance(value, dict):
        return {}
    return {str(key): str(raw) for key, raw in value.items() if raw is not None}