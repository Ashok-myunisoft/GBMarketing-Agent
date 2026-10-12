"""Gemini API + Google Search grounding for GST and turnover research."""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

from core.config import settings
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(template: str, **fields: "Optional[str]") -> str:
    return _PLACEHOLDER.sub(lambda match: fields.get(match.group(1)) or "", template)


def _parse_json(text: str) -> dict:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
    except (TypeError, json.JSONDecodeError):
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start >= 0 and end > start:
        result = json.loads(cleaned[start : end + 1])
        if isinstance(result, dict):
            return result
    raise ValueError("Gemini response did not contain a JSON object")


@dataclass
class AIResearchResult:
    """One researched field; downstream code validates value and sources."""

    status: str = "not_found"
    value: Optional[str] = None
    currency: Optional[str] = None
    financial_year: Optional[str] = None
    metric: Optional[str] = None
    evidence: str = ""
    confidence_label: Optional[str] = None
    sources: "list[str]" = field(default_factory=list)


class GeminiResearchClient:
    """Thin client for Gemini Flash with Google Search grounding."""

    def __init__(
        self,
        api_key: "Optional[str]" = None,
        model: "Optional[str]" = None,
        timeout_seconds: "Optional[float]" = None,
    ):
        self._api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self._model = model or settings.GEMINI_RESEARCH_MODEL
        timeout = timeout_seconds or settings.GEMINI_RESEARCH_TIMEOUT_SECONDS
        self._client = (
            genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=int(timeout * 1000)),
            )
            if self._api_key else None
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    def search_gst(self, **company_fields: "Optional[str]") -> AIResearchResult:
        payload, sources = self._research("gst_search", **company_fields)
        if not payload:
            return AIResearchResult()
        return AIResearchResult(
            status=payload.get("status") or "not_found",
            value=_clean_str(payload.get("gstin")),
            evidence=_clean_str(payload.get("evidence")) or "",
            confidence_label=_clean_str(payload.get("confidence")),
            sources=sources,
        )

    def search_turnover(self, **company_fields: "Optional[str]") -> AIResearchResult:
        payload, sources = self._research("turnover_search", **company_fields)
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
            sources=sources,
        )

    def _research(self, prompt_name: str, **company_fields):
        company_name = company_fields.get("company_name")
        if not self._client:
            logger.warning("[GEMINI_RESEARCH] prompt=%s skipped: GEMINI_API_KEY is not configured", prompt_name)
            return None, []

        try:
            prompt = _render(PromptService.load(prompt_name), **company_fields)
        except Exception:
            logger.exception("[GEMINI_RESEARCH] prompt=%s failed to render", prompt_name)
            return None, []

        # Interactions API exposes Google Search grounding citations alongside
        # the final model output. The prompt templates define the JSON contract.
        prompt += (
            "\n\nUse Google Search for this research. Return ONLY valid JSON "
            "matching the OUTPUT structure in the instructions above. "
            "Do not include markdown or text outside the JSON object."
        )
        for attempt in range(2):
            request_prompt = prompt
            if attempt:
                request_prompt += "\nYour previous response was not valid JSON. Return only valid JSON."
            try:
                interaction = self._client.interactions.create(
                    model=self._model,
                    input=request_prompt,
                    tools=[{"type": "google_search"}],
                )
            except Exception:
                logger.exception(
                    "[GEMINI_RESEARCH] prompt=%s company=%s request failed",
                    prompt_name, company_name,
                )
                return None, []

            sources = _interaction_sources(interaction)
            try:
                payload = _parse_json(interaction.output_text or "")
            except Exception:
                logger.warning(
                    "[GEMINI_RESEARCH] prompt=%s company=%s invalid JSON (attempt %s)",
                    prompt_name, company_name, attempt + 1,
                )
                continue

            # Use provider grounding metadata rather than trusting URLs
            # written by the model in its JSON response.
            logger.info(
                "[GEMINI_RESEARCH] prompt=%s company=%s status=%s grounded_sources=%s",
                prompt_name, company_name, payload.get("status"), len(sources),
            )
            return payload, sources

        return None, []


def _interaction_sources(interaction) -> list[str]:
    urls: list[str] = []
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for block in getattr(step, "content", None) or []:
            for annotation in getattr(block, "annotations", None) or []:
                uri = getattr(annotation, "uri", None) or getattr(annotation, "url", None)
                if isinstance(uri, str) and uri.startswith(("https://", "http://")) and uri not in urls:
                    urls.append(uri)
    return urls


def _clean_str(value) -> "Optional[str]":
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None
