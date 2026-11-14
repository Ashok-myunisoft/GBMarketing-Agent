"""Gemini API + Google Search grounding for GST and turnover research."""

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from google import genai
from google.genai import types

from core.config import settings
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_QUOTA_LOCK = threading.Lock()
_QUOTA_PROBE_LOCK = threading.Lock()
_QUOTA_EXHAUSTED = False
_QUOTA_PROBED = False
_QUOTA_SKIP_LOGGED = False


def _is_quota_exhausted(error: Exception) -> bool:
    """Distinguish exhausted account quota from ordinary transient errors."""
    status_code = getattr(error, "status_code", None) or getattr(error, "code", None)
    message = str(error).casefold()
    return status_code == 429 or any(
        marker in message
        for marker in ("exceeded your current quota", "too_many_requests", "quota_exceeded")
    )


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
                # A quota-exhausted 429 is not made better by SDK backoff.
                # Retry only short-lived transport/server failures, so the
                # caller can open the quota circuit promptly and continue with
                # values already present on the discovery record.
                http_options=types.HttpOptions(
                    timeout=int(timeout * 1000),
                    retry_options=types.HttpRetryOptions(
                        attempts=3,
                        initial_delay=0.5,
                        max_delay=2.0,
                        exp_base=2.0,
                        jitter=0.0,
                        http_status_codes=[408, 500, 502, 503, 504],
                    ),
                ),
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
        global _QUOTA_EXHAUSTED, _QUOTA_PROBED, _QUOTA_SKIP_LOGGED

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
        # Gemini has no lightweight endpoint that reports remaining project
        # quota. Treat the first actual research request as the quota check
        # and serialize only that probe: if it gets a 429, queued company and
        # field requests see the open circuit instead of all calling Gemini.
        quota_probe = False
        with _QUOTA_LOCK:
            quota_exhausted = _QUOTA_EXHAUSTED
            quota_probed = _QUOTA_PROBED
        if quota_exhausted:
            self._log_quota_skip_once()
            return None, []
        if not quota_probed:
            _QUOTA_PROBE_LOCK.acquire()
            with _QUOTA_LOCK:
                quota_exhausted = _QUOTA_EXHAUSTED
                quota_probed = _QUOTA_PROBED
            if quota_exhausted:
                _QUOTA_PROBE_LOCK.release()
                self._log_quota_skip_once()
                return None, []
            quota_probe = not quota_probed
            if not quota_probe:
                _QUOTA_PROBE_LOCK.release()

        try:
            for attempt in range(2):
                request_prompt = prompt
                if attempt:
                    request_prompt += "\nYour previous response was not valid JSON. Return only valid JSON."
                request_started = time.monotonic()
                try:
                    interaction = self._client.interactions.create(
                        model=self._model,
                        input=request_prompt,
                        tools=[{"type": "google_search"}],
                    )
                except Exception as exc:
                    elapsed_ms = round((time.monotonic() - request_started) * 1000)
                    if _is_quota_exhausted(exc):
                        with _QUOTA_LOCK:
                            first_quota_error = not _QUOTA_EXHAUSTED
                            _QUOTA_EXHAUSTED = True
                            _QUOTA_PROBED = True
                        if first_quota_error:
                            logger.error(
                                "[GEMINI_RESEARCH] quota exhausted; skipping remaining GST/turnover "
                                "research and retaining existing discovery values "
                                "(prompt=%s company=%s elapsed_ms=%d): %s",
                                prompt_name, company_name, elapsed_ms, exc,
                            )
                        return None, []
                    logger.exception(
                        "[GEMINI_RESEARCH] prompt=%s company=%s attempt=%d elapsed_ms=%d request failed",
                        prompt_name, company_name, attempt + 1, elapsed_ms,
                    )
                    return None, []

                if quota_probe:
                    with _QUOTA_LOCK:
                        _QUOTA_PROBED = True
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
                    "[PERF] gemini_research prompt=%s company=%s attempt=%d elapsed_ms=%d status=%s grounded_sources=%d",
                    prompt_name, company_name, attempt + 1,
                    round((time.monotonic() - request_started) * 1000),
                    payload.get("status"), len(sources),
                )
                return payload, sources

            return None, []
        finally:
            if quota_probe:
                _QUOTA_PROBE_LOCK.release()

    @staticmethod
    def _log_quota_skip_once() -> None:
        global _QUOTA_SKIP_LOGGED
        with _QUOTA_LOCK:
            if _QUOTA_SKIP_LOGGED:
                return
            _QUOTA_SKIP_LOGGED = True
        logger.warning(
            "[GEMINI_RESEARCH] quota circuit open; skipping GST/turnover research "
            "and retaining existing source values until backend restart"
        )


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
