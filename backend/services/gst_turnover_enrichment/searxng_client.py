"""SearXNG search client - the search/discovery layer for GST/turnover
enrichment, replacing Firecrawl Cloud Search in that one path only.

Talks directly to a self-hosted SearXNG instance's JSON API
(GET {SEARXNG_URL}/search?q=...&format=json) via httpx - the same house
style firecrawl_cloud_search.py and services/tavily/tavily_search.py use
for their own JSON APIs.

SearXNG is discovery only: it returns candidate URLs plus a short snippet
(title/content), never full page content. Actual page crawling for GST/
turnover extraction still goes through Crawl4AI - see service.py's
_extract_candidates()/_crawl4ai_fallback().

Never raises: every failure (timeout, connection error, non-2xx status,
malformed JSON, an empty/missing "results" array) is caught here and
degrades to an empty list, so a single company's GST/turnover lookup can
never crash the rest of the batch - callers should treat [] exactly like
"nothing found", not distinguish it from a real empty search.
"""

import logging
from dataclasses import dataclass

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearXNGResult:
    """One SearXNG search result, shaped to drop into the same ranking/
    extraction code service.py already uses for Firecrawl Cloud Search
    results (see FirecrawlSearchResult in firecrawl_client.py) - `markdown`
    is always "" here (SearXNG never returns full page content, only a
    snippet), kept only so existing code reading `result.markdown` (always
    empty for a search-only result anyway) needs no changes.
    """

    url: str = ""
    title: str = ""
    description: str = ""
    markdown: str = ""
    position: int = 0
    score: float = 0.0
    engine: str = ""


class SearXNGSearchClient:
    """Thin, defensive wrapper around a self-hosted SearXNG instance's
    JSON search API."""

    def __init__(self, base_url: "str | None" = None, timeout_seconds: "float | None" = None):
        self._base_url = (base_url if base_url is not None else settings.SEARXNG_URL).rstrip("/")
        self._timeout_seconds = timeout_seconds or settings.SEARXNG_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url)

    def search(self, query: str, limit: int = 10) -> "list[SearXNGResult]":
        if not self._base_url:
            logger.warning("[SEARXNG] query=%s status=skipped reason=not_configured", query)
            return []
        if not query or not query.strip():
            return []

        params = {"q": query, "format": "json", "categories": "general"}

        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.get(f"{self._base_url}/search", params=params)
        except httpx.TimeoutException:
            logger.warning("[SEARXNG] timeout query=%s", query)
            return []
        except httpx.RequestError as ex:
            logger.warning("[SEARXNG] error query=%s reason=%s", query, ex)
            return []

        if response.status_code >= 400:
            logger.warning(
                "[SEARXNG] error query=%s reason=http_%s", query, response.status_code
            )
            return []

        try:
            body = response.json()
        except ValueError:
            logger.warning("[SEARXNG] error query=%s reason=malformed_json", query)
            return []

        if not isinstance(body, dict):
            logger.warning("[SEARXNG] error query=%s reason=malformed_response", query)
            return []

        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            logger.info("[SEARXNG] query=%s results=0", query)
            return []

        results: "list[SearXNGResult]" = []
        for index, item in enumerate(raw_results[:limit]):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            results.append(SearXNGResult(
                url=url,
                title=str(item.get("title") or ""),
                description=str(item.get("content") or ""),
                position=index,
                score=score,
                engine=str(item.get("engine") or ""),
            ))

        logger.info("[SEARXNG] query=%s results=%d", query, len(results))
        return results
