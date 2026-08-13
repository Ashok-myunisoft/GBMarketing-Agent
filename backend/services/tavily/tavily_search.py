"""
Tavily-backed SearchProvider. Talks to Tavily's REST API directly via httpx
(the same house style as services/runpod_client.py and services/llm_services.py
use for their own JSON APIs), so no extra SDK dependency is needed.

Returns an empty result list - never raises - whenever no API key is
configured or the request fails, so callers can treat "Tavily unavailable"
and "Tavily found nothing" the same way: as an empty candidate source.
"""

import logging
import threading
import time
from typing import ClassVar, Optional

import httpx

from core.config import settings
from services.tavily.base import SearchProvider, SearchResult

logger = logging.getLogger(__name__)


class TavilySearchService(SearchProvider):
    # HTTP statuses that mean "this key/account cannot be used at all right
    # now" (invalid key, forbidden, or Tavily's own 432 "exceeds your plan's
    # set usage limit") - as opposed to "this one request failed", which is
    # worth retrying on the next query/company. Shared across every instance
    # and every caller that creates its own TavilySearchService, so that once
    # Tavily reports it's unusable, every subsequent call anywhere in the
    # process skips the network entirely instead of repeating a
    # guaranteed-to-fail request for every remaining company in the batch.
    # Resets only on process restart - a plan upgrade or new key takes
    # effect on the next run anyway.
    _TERMINAL_STATUS_CODES = {401, 403, 432}
    _unavailable_reason: ClassVar[Optional[str]] = None

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 20.0,
    ):
        self._api_key = api_key or settings.TAVILY_API_KEY
        self._base_url = (base_url or settings.TAVILY_API_BASE_URL).rstrip("/")
        self._timeout = timeout
        # A job-level cache lives on the service instance.  It prevents
        # equivalent queries from spending quota/network time twice while
        # keeping results and customer data out of shared infrastructure.
        self._cache: dict[tuple, list[SearchResult]] = {}
        self._cache_lock = threading.Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key) and TavilySearchService._unavailable_reason is None

    def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        include_domains: "Optional[list[str]]" = None,
        exclude_domains: "Optional[list[str]]" = None,
        country: Optional[str] = "india",
    ) -> "list[SearchResult]":
        if not self.is_configured:
            return []

        normalized_query = " ".join(query.lower().split())
        cache_key = (
            normalized_query, max_results or settings.TAVILY_MAX_RESULTS_PER_QUERY,
            tuple(sorted(domain.lower() for domain in include_domains or [])),
            tuple(sorted(domain.lower() for domain in exclude_domains or [])), country or "",
        )
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info("[PERF] Tavily cache hit: query=%s", normalized_query)
            return list(cached)

        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max_results or settings.TAVILY_MAX_RESULTS_PER_QUERY,
            "search_depth": "advanced",
            "include_answer": False,
            "include_raw_content": True,
        }
        if include_domains:
            # Restricts results to specific domains (e.g. trusted business
            # directories).
            payload["include_domains"] = list(include_domains)
        if exclude_domains:
            payload["exclude_domains"] = list(exclude_domains)
        if country:
            payload["country"] = country

        try:
            started = time.monotonic()
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(f"{self._base_url}/search", json=payload)
            logger.info("[PERF] Tavily request: %d ms", round((time.monotonic() - started) * 1000))
        except httpx.RequestError as ex:
            logger.warning("Tavily search failed for query '%s': %s", query, ex)
            return []

        if response.status_code in self._TERMINAL_STATUS_CODES:
            TavilySearchService._unavailable_reason = self._error_detail(response)
            logger.error(
                "Tavily is unusable for the rest of this run (HTTP %s): %s - "
                "skipping all further Tavily calls until the process restarts.",
                response.status_code,
                TavilySearchService._unavailable_reason,
            )
            return []

        try:
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPStatusError, ValueError) as ex:
            logger.warning("Tavily search failed for query '%s': %s", query, ex)
            return []

        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        results: "list[SearchResult]" = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title") or "",
                    content=item.get("content") or "",
                    score=float(item.get("score") or 0.0),
                    raw_content=item.get("raw_content"),
                )
            )
        with self._cache_lock:
            self._cache[cache_key] = list(results)
        return results

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                return str(detail.get("error") or detail)
            if detail:
                return str(detail)
        return response.text[:200]
