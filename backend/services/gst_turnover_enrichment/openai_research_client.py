"""OpenAI Responses API + hosted web_search research client - the search
AND extraction mechanism for GST and turnover discovery, replacing
SearXNG+Crawl4AI in that one path only (see service.py).

The search query strategy, source preferences, and extraction/validation
rules all live in the prompt templates (app/prompts/gst_search.md,
app/prompts/turnover_search.md) - this module only renders those templates
with the company's own details and parses the model's structured JSON
response. It never builds a search query itself; changing the query
pattern only ever requires editing the .md files, never this file.

Never raises: every failure (no API key configured, network error,
malformed JSON, an out-of-shape response) is caught here and degrades to
a not-found result, so a single company's GST/turnover lookup can never
crash the rest of the batch.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from core.config import settings
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# The Responses API's web_search tool: verified by hand that the model
# otherwise frequently ignores a plain "return only JSON" instruction in
# the prompt and answers in free-form prose with inline citations instead -
# these `text.format` JSON-schema constraints are what actually guarantee
# parseable structured output, not the prompt wording alone. The prompt
# templates' own OUTPUT FORMAT section is still authoritative for the
# meaning/semantics of each field; these schemas only mirror its shape so
# the API enforces it.
_GST_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["found", "not_found"]},
        "gstin": {"type": ["string", "null"]},
        "evidence": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "gstin", "evidence", "confidence", "sources"],
    "additionalProperties": False,
}

_TURNOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["found", "not_found"]},
        "turnover": {"type": ["string", "null"]},
        "currency": {"type": ["string", "null"]},
        "financial_year": {"type": ["string", "null"]},
        "metric": {"type": ["string", "null"]},
        "evidence": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "status", "turnover", "currency", "financial_year", "metric",
        "evidence", "confidence", "sources",
    ],
    "additionalProperties": False,
}


def _render(template: str, **fields: "Optional[str]") -> str:
    """Replaces every ``{{field}}`` token in the template with the given
    value (blank when not provided/empty). Purely mechanical substitution -
    the query pattern, instructions, and output format all stay exactly
    what the .md file says; this never adds, removes, or rewrites any of
    that text."""
    return _PLACEHOLDER.sub(lambda m: (fields.get(m.group(1)) or ""), template)


def _parse_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return json.loads(cleaned)


@dataclass
class AIResearchResult:
    """One field's (GST or turnover) research result. ``value`` is always
    the plain extracted value (a GSTIN, or a turnover figure with its
    original currency/unit as reported) - never a substitute for the
    existing GST checksum / turnover-extraction validation, which the
    caller (service.py) still runs on this value before trusting it."""

    status: str = "not_found"  # "found" | "not_found"
    value: Optional[str] = None
    currency: Optional[str] = None
    financial_year: Optional[str] = None
    metric: Optional[str] = None
    evidence: str = ""
    confidence_label: Optional[str] = None  # the model's own "high"/"medium"/"low" self-assessment
    sources: "list[str]" = field(default_factory=list)


class OpenAIResearchClient:
    """Thin, defensive wrapper around the OpenAI Responses API's hosted
    web_search tool, for GST/turnover research only. Uses
    OPENAI_RESEARCH_API_KEY/OPENAI_RESEARCH_MODEL - deliberately separate
    from OPENAI_API_KEY/OPENAI_MODEL, which are actually RunPod credentials
    used by unrelated AI features (see core/config.py)."""

    def __init__(
        self, api_key: "Optional[str]" = None, model: "Optional[str]" = None,
        timeout_seconds: "Optional[float]" = None,
    ):
        self._api_key = api_key if api_key is not None else settings.OPENAI_RESEARCH_API_KEY
        self._model = model or settings.OPENAI_RESEARCH_MODEL
        self._timeout_seconds = timeout_seconds or settings.OPENAI_RESEARCH_TIMEOUT_SECONDS
        self._client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds) if self._api_key else None

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def search_gst(self, **company_fields: "Optional[str]") -> AIResearchResult:
        payload = self._research("gst_search", "gst_result", _GST_SCHEMA, **company_fields)
        if not payload:
            return AIResearchResult()
        return AIResearchResult(
            status=payload.get("status") or "not_found",
            value=_clean_str(payload.get("gstin")),
            evidence=_clean_str(payload.get("evidence")) or "",
            confidence_label=_clean_str(payload.get("confidence")),
            sources=_clean_sources(payload.get("sources")),
        )

    def search_turnover(self, **company_fields: "Optional[str]") -> AIResearchResult:
        payload = self._research("turnover_search", "turnover_result", _TURNOVER_SCHEMA, **company_fields)
        if not payload:
            return AIResearchResult()
        return AIResearchResult(
            status=payload.get("status") or "not_found",
            value=_clean_str(payload.get("turnover")),
            currency=_clean_str(payload.get("currency")),
            financial_year=_clean_str(payload.get("financial_year")),
            metric=_clean_str(payload.get("metric")),
            evidence=_clean_str(payload.get("evidence")) or "",
            confidence_label=_clean_str(payload.get("confidence")),
            sources=_clean_sources(payload.get("sources")),
        )

    def _research(
        self, prompt_name: str, schema_name: str, schema: dict, **company_fields: "Optional[str]"
    ) -> "Optional[dict]":
        company_name = company_fields.get("company_name")
        if not self._client:
            logger.warning("[AI_RESEARCH] prompt=%s status=skipped reason=not_configured", prompt_name)
            return None

        try:
            template = PromptService.load(prompt_name)
            rendered = _render(template, **company_fields)
        except Exception:
            logger.exception("[AI_RESEARCH] prompt=%s failed to render template", prompt_name)
            return None

        for attempt in range(2):
            request_input = rendered
            if attempt:
                request_input += (
                    "\n\nPerform one independent second web-search pass using the alternative "
                    "queries in this prompt. Re-check the exact company identity before returning JSON."
                )
            try:
                response = self._client.responses.create(
                    model=self._model,
                    input=request_input,
                    tools=[{"type": "web_search"}],
                    text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
                )
                text = response.output_text
            except Exception:
                logger.exception(
                    "[AI_RESEARCH] prompt=%s company=%s request failed", prompt_name, company_name
                )
                return None

            try:
                payload = _parse_json(text)
            except Exception:
                logger.warning(
                    "[AI_RESEARCH] prompt=%s company=%s could not parse model output as JSON (attempt %s)",
                    prompt_name, company_name, attempt + 1,
                )
                continue

            if not isinstance(payload, dict):
                logger.warning(
                    "[AI_RESEARCH] prompt=%s company=%s malformed response shape (attempt %s)",
                    prompt_name, company_name, attempt + 1,
                )
                continue

            logger.info(
                "[AI_RESEARCH] prompt=%s company=%s status=%s attempt=%s",
                prompt_name, company_name, payload.get("status"), attempt + 1,
            )
            if payload.get("status") != "not_found" or attempt == 1:
                return payload

        return None


def _clean_str(value) -> "Optional[str]":
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _clean_sources(value) -> "list[str]":
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
