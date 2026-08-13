"""
Firecrawl Cloud Search client.

Talks directly to Firecrawl's hosted Search API
(POST https://api.firecrawl.dev/v2/search) via httpx - the same house style
services/tavily/tavily_search.py and services/runpod_client.py use for their
own JSON APIs, so no extra SDK dependency is needed for this call.

This is deliberately separate from FirecrawlClient's self-hosted `.scrape()`
path in firecrawl_client.py: GST/turnover discovery must use Firecrawl Cloud
Search exclusively, while page scraping keeps using the existing self-hosted
Firecrawl instance untouched.

Never logs or raises the API key itself - only a boolean "configured" state
and a short failure reason.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

FIRECRAWL_CLOUD_SEARCH_URL = "https://api.firecrawl.dev/v2/search"


class FirecrawlCloudSearchError(Exception):
    """One Firecrawl Cloud Search request could not be completed.

    ``retryable`` tells the caller whether trying again is worth it
    (timeout/network/5xx/rate-limit) or futile (bad key, malformed
    response) - the retry/backoff decision itself lives in the caller
    (FirecrawlClient.search), not in this HTTP-contract-only client.
    """

    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class CloudSearchResult:
    url: str = ""
    title: str = ""
    description: str = ""
    position: int = 0


class FirecrawlCloudSearchClient:
    """Thin, defensive wrapper around Firecrawl Cloud Search.

    Handles: success=false, missing "data", missing "web", empty results,
    HTTP errors, timeouts, rate limiting, invalid API key, and malformed
    JSON - every failure raises FirecrawlCloudSearchError instead of
    crashing the caller.
    """

    def __init__(self, api_key: Optional[str] = None, timeout_seconds: Optional[float] = None):
        self._api_key = api_key if api_key is not None else settings.FIRECRAWL_API_KEY
        self._timeout_seconds = timeout_seconds or settings.FIRECRAWL_SEARCH_TIMEOUT_SECONDS

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def search(self, query: str, limit: int = 10) -> "list[CloudSearchResult]":
        if not self._api_key:
            raise FirecrawlCloudSearchError("FIRECRAWL_API_KEY is not configured", retryable=False)

        if not query or not query.strip():
            return []

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": query, "limit": limit}

        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(FIRECRAWL_CLOUD_SEARCH_URL, headers=headers, json=payload)
        except httpx.TimeoutException as ex:
            raise FirecrawlCloudSearchError("timeout", retryable=True) from ex
        except httpx.RequestError as ex:
            raise FirecrawlCloudSearchError(f"network_error: {ex}", retryable=True) from ex

        if response.status_code in (401, 403):
            raise FirecrawlCloudSearchError("invalid_api_key", retryable=False)
        if response.status_code == 429:
            raise FirecrawlCloudSearchError("rate_limited", retryable=True)
        if response.status_code >= 500:
            raise FirecrawlCloudSearchError(f"http_{response.status_code}", retryable=True)
        if response.status_code >= 400:
            raise FirecrawlCloudSearchError(f"http_{response.status_code}", retryable=False)

        try:
            body = response.json()
        except ValueError as ex:
            raise FirecrawlCloudSearchError("malformed_response", retryable=False) from ex

        if not isinstance(body, dict):
            raise FirecrawlCloudSearchError("malformed_response", retryable=False)

        if not body.get("success", False):
            raise FirecrawlCloudSearchError(f"unsuccessful_response: {body.get('error') or 'unknown'}", retryable=False)

        data = body.get("data")
        if not isinstance(data, dict):
            return []

        web_results = data.get("web")
        if not isinstance(web_results, list):
            return []

        results: "list[CloudSearchResult]" = []
        for item in web_results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            try:
                position = int(item.get("position") or 0)
            except (TypeError, ValueError):
                position = 0
            results.append(CloudSearchResult(
                url=url,
                title=str(item.get("title") or ""),
                description=str(item.get("description") or ""),
                position=position,
            ))
        return results[:limit]
