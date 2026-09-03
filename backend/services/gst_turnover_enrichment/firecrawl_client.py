import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from firecrawl import Firecrawl
from firecrawl.v2.utils.error_handler import (
    BadRequestError,
    PaymentRequiredError,
    UnauthorizedError,
    WebsiteNotSupportedError,
)

from core.config import settings
from services.gst_turnover_enrichment.firecrawl_cloud_search import (
    FirecrawlCloudSearchClient,
    FirecrawlCloudSearchError,
)


logger = logging.getLogger(__name__)

# Shared across isolated enrichment workers.  A quota error cannot be fixed by
# retrying a different query, so this prevents a 50-company job from flooding
# Firecrawl with identical HTTP 402 failures.
_CLOUD_SEARCH_BREAKER_LOCK = threading.Lock()
_CLOUD_SEARCH_PAUSED_UNTIL = 0.0


# ============================================================
# ERROR TYPES
# ============================================================

_PERMANENT_ERRORS = (
    BadRequestError,
    UnauthorizedError,
    WebsiteNotSupportedError,
    PaymentRequiredError,
)


# ============================================================
# RESULT MODEL
# ============================================================

@dataclass
class FirecrawlPage:
    url: str
    html: str = ""
    markdown: str = ""
    success: bool = False
    error: Optional[str] = None
    # "ANTI_BOT" | "RATE_LIMITED" | "SCRAPE_FAILED" | None (success)
    error_code: Optional[str] = None


@dataclass
class FirecrawlSearchResult:
    """A compact, grounded result returned by Firecrawl Cloud Search
    (POST https://api.firecrawl.dev/v2/search). ``markdown`` is populated
    only when a result was subsequently scraped for more content - Cloud
    Search itself returns title/description/position, not page content."""

    url: str = ""
    title: str = ""
    markdown: str = ""
    description: str = ""
    position: int = 0




_CACHE_LOCK = threading.RLock()

_GLOBAL_CACHE: dict[str, FirecrawlPage] = {}




_RATE_LOCK = threading.RLock()

_LAST_REQUEST_TIME = 0.0


def _wait_for_rate_slot() -> None:

    global _LAST_REQUEST_TIME

    limit = max(
        1,
        int(
            settings.FIRECRAWL_MAX_REQUESTS_PER_MINUTE
        ),
    )

    interval = 60.0 / float(limit)

    while True:

        with _RATE_LOCK:

            now = time.monotonic()

            elapsed = (
                now - _LAST_REQUEST_TIME
            )

            if elapsed >= interval:

                _LAST_REQUEST_TIME = now

                return

            wait_seconds = (
                interval - elapsed
            )

            wait_seconds = max(
                0.05,
                wait_seconds,
            )

            logger.info(
                "[FIRECRAWL_RATE] "
                "limit=%s/min "
                "interval=%.2fs "
                "waiting=%.2fs",
                limit,
                interval,
                wait_seconds,
            )

        time.sleep(wait_seconds)


_FIRECRAWL_SEMAPHORE = threading.BoundedSemaphore(
    max(
        1,
        int(
            settings.FIRECRAWL_MAX_CONCURRENCY
        ),
    )
)


def _normalize_url(url: str) -> str:
    """
    Normalize URLs for caching.

    Examples:

        https://example.com
        https://example.com/

    become the same cache key.
    """

    value = (url or "").strip()

    if not value:
        return ""

    return value.rstrip("/")



def _is_rate_limit_error(
    error: Exception,
) -> bool:

    message = str(error).lower()

    patterns = (
        "rate limit",
        "rate_limit",
        "429",
        "too many requests",
        "requests/min",
        "remaining (req/min): 0",
        "rate limit exceeded",
    )

    return any(
        pattern in message
        for pattern in patterns
    )


# Self-hosted Firecrawl reports anti-bot blocks as a 200 response with
# {"success": false, "code": "SCRAPE_RETRY_LIMIT", "error": "...document_antibot..."}.
# The SDK surfaces that as a plain Exception(body["error"]) - detect it by
# message so callers can stop immediately instead of retrying a blocked URL.
_ANTI_BOT_PATTERNS = (
    "antibot",
    "anti-bot",
    "anti_bot",
    "retry limit",
    "scrape_retry_limit",
)

_DNS_ERROR_PATTERNS = (
    "dns", "nxdomain", "could not resolve host", "name not resolved",
    "err_name_not_resolved", "unknown host",
)


def _is_anti_bot_error(
    error: Exception,
) -> bool:

    message = str(error).lower()

    return any(
        pattern in message
        for pattern in _ANTI_BOT_PATTERNS
    )


def _is_dns_error(error: Exception) -> bool:
    return any(pattern in str(error).lower() for pattern in _DNS_ERROR_PATTERNS)


# ============================================================
# CLIENT
# ============================================================

class FirecrawlClient:
    """
    Cached, rate-limited, non-fatal Firecrawl wrapper.

    Multiple EnrichmentAgent workers may create separate
    instances of this class, but they all share:

        - global cache
        - global rate limiter
        - global concurrency semaphore
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
    ):

        self._api_key = (
            api_key
            or settings.FIRECRAWL_API_KEY
        )

        # A self-hosted Firecrawl instance needs no API key at all - only a
        # reachable api_url. `api_url=""` is a deliberate "not configured"
        # override (used by tests); anything else, including None, falls
        # back to FIRECRAWL_API_URL from settings.
        self._api_url = (
            api_url
            if api_url is not None
            else settings.FIRECRAWL_API_URL
        )

        self._client = (
            Firecrawl(
                api_key=self._api_key,
                api_url=self._api_url,

                # Firecrawl rate limiting is handled by this
                # application-level wrapper.
                #
                # Do not allow the SDK to create an additional
                # retry storm when the account is already
                # rate-limited.
                max_retries=0,

                timeout=(
                    settings.FIRECRAWL_TIMEOUT_MS
                    / 1000
                ),
            )
            if self._api_url
            else None
        )

        # GST/turnover search always goes through Firecrawl Cloud
        # (https://api.firecrawl.dev/v2/search), never the self-hosted
        # instance above - independent of whichever api_url was resolved
        # for .scrape(). Uses the same FIRECRAWL_API_KEY.
        self._cloud_search = FirecrawlCloudSearchClient(api_key=self._api_key)

    # --------------------------------------------------------
    # CONFIGURATION
    # --------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    @staticmethod
    def _get_cached(
        cache_key: str,
    ) -> Optional[FirecrawlPage]:

        with _CACHE_LOCK:

            return _GLOBAL_CACHE.get(
                cache_key
            )

    @staticmethod
    def _set_cached(
        cache_key: str,
        page: FirecrawlPage,
    ) -> None:

        # Permanent anti-bot/DNS failures are reusable for duplicate leads;
        # transient failures and rate limits intentionally remain retryable.
        if not page.success and page.error_code not in {"ANTI_BOT", "DNS_FAILED"}:
            return

        with _CACHE_LOCK:

            _GLOBAL_CACHE[
                cache_key
            ] = page

    # --------------------------------------------------------
    # PUBLIC SCRAPE
    # --------------------------------------------------------

    def scrape(
        self,
        url: str,
    ) -> FirecrawlPage:
        """
        Fetch one URL through Firecrawl.

        Behavior:

        1. Normalize URL.
        2. Check global cache.
        3. Acquire global concurrency slot.
        4. Wait for global rate-limit slot.
        5. Call Firecrawl once.
        6. Cache successful response.
        7. Return failure without crashing enrichment.
        """

        normalized_url = _normalize_url(
            url
        )

        if not normalized_url:

            return FirecrawlPage(
                url=url,
                error="Empty URL",
                error_code="NOT_FOUND",
            )

        # ----------------------------------------------------
        # CACHE
        # ----------------------------------------------------

        cached = self._get_cached(
            normalized_url
        )

        if cached is not None:

            logger.info(
                "[FIRECRAWL] "
                "url=%s "
                "status=cache_hit",
                normalized_url,
            )

            return cached

        if not self._client:

            logger.warning(
                "[FIRECRAWL] "
                "url=%s "
                "status=failed "
                "reason=Firecrawl not configured "
                "action=continue_without_enrichment",
                normalized_url,
            )

            return FirecrawlPage(
                url=normalized_url,
                error="Firecrawl not configured",
                error_code="NOT_FOUND",
            )

        # ----------------------------------------------------
        # GLOBAL CONCURRENCY
        # ----------------------------------------------------

        acquired = False

        try:

            _FIRECRAWL_SEMAPHORE.acquire()

            acquired = True

            # ------------------------------------------------
            # GLOBAL RATE LIMIT
            # ------------------------------------------------

            _wait_for_rate_slot()

            started = time.monotonic()

            logger.info(
                "[FIRECRAWL] "
                "url=%s "
                "status=starting",
                normalized_url,
            )

            # ------------------------------------------------
            # ACTUAL FIRECRAWL REQUEST
            # ------------------------------------------------

            document = self._client.scrape(
                normalized_url,
                formats=[
                    "html",
                    "markdown",
                ],
                timeout=(
                    settings.FIRECRAWL_TIMEOUT_MS
                ),
            )

            duration_ms = round(
                (
                    time.monotonic()
                    - started
                )
                * 1000
            )

            html = (
                getattr(
                    document,
                    "html",
                    None,
                )
                or getattr(
                    document,
                    "raw_html",
                    None,
                )
                or ""
            )

            markdown = (
                getattr(
                    document,
                    "markdown",
                    None,
                )
                or ""
            )

            page = FirecrawlPage(
                url=normalized_url,
                html=html,
                markdown=markdown,
                success=True,
            )

            # ------------------------------------------------
            # CACHE SUCCESS
            # ------------------------------------------------

            self._set_cached(
                normalized_url,
                page,
            )

            logger.info(
                "[FIRECRAWL] "
                "url=%s "
                "status=success "
                "duration=%dms",
                normalized_url,
                duration_ms,
            )

            return page

        except _PERMANENT_ERRORS as ex:

            logger.info(
                "[FIRECRAWL] "
                "url=%s "
                "status=failed "
                "reason=%s "
                "action=continue_without_enrichment",
                normalized_url,
                ex,
            )

            return FirecrawlPage(
                url=normalized_url,
                error=str(ex),
                error_code="SCRAPE_FAILED",
            )

        except Exception as ex:

            duration_ms = round(
                (
                    time.monotonic()
                    - started
                )
                * 1000
            ) if "started" in locals() else 0

            if _is_anti_bot_error(ex):

                # Anti-bot blocks do not improve on a second attempt within
                # the same run. Log distinctly and return immediately -
                # never retry the same URL, let the caller's existing
                # fallback take over.
                logger.info(
                    "[FIRECRAWL] "
                    "url=%s "
                    "status=anti_bot "
                    "duration=%dms "
                    "reason=%s "
                    "action=continue_without_enrichment",
                    normalized_url,
                    duration_ms,
                    ex,
                )

                error_code = "ANTI_BOT"

            elif _is_dns_error(ex):

                logger.info(
                    "[FIRECRAWL] url=%s status=dns_failed duration=%dms reason=%s",
                    normalized_url, duration_ms, ex,
                )

                error_code = "DNS_FAILED"

            elif _is_rate_limit_error(ex):

                logger.warning(
                    "[FIRECRAWL] "
                    "url=%s "
                    "status=rate_limited "
                    "duration=%dms "
                    "reason=%s "
                    "action=continue_without_enrichment",
                    normalized_url,
                    duration_ms,
                    ex,
                )

                error_code = "RATE_LIMITED"

            else:

                logger.warning(
                    "[FIRECRAWL] "
                    "url=%s "
                    "status=failed "
                    "duration=%dms "
                    "reason=%s "
                    "action=continue_without_enrichment",
                    normalized_url,
                    duration_ms,
                    ex,
                )

                error_code = "SCRAPE_FAILED"

            page = FirecrawlPage(
                url=normalized_url,
                error=str(ex),
                error_code=error_code,
            )
            self._set_cached(normalized_url, page)
            return page

        finally:

            if acquired:

                _FIRECRAWL_SEMAPHORE.release()

    def search(self, query: str, limit: int = 5, company: Optional[str] = None) -> "list[FirecrawlSearchResult]":
        """Search via Firecrawl Cloud Search (POST https://api.firecrawl.dev/v2/search).

        This is intentionally separate from :meth:`scrape`, which keeps using
        the self-hosted Firecrawl instance: GST/turnover discovery supplies
        the company-name query to Firecrawl Cloud and does not use a company
        website URL at all. Never raises - any failure (missing key, bad
        auth, timeout, rate limit, malformed response) degrades to an empty
        result list so a single company's GST/turnover lookup can never stop
        the rest of the batch.
        """
        global _CLOUD_SEARCH_PAUSED_UNTIL

        if not query or not query.strip():
            return []

        if not self._cloud_search.is_configured:
            logger.error(
                "[FIRECRAWL_SEARCH] company=%s query=%s status=failed reason=configuration_error "
                "detail=\"FIRECRAWL_API_KEY is not set\"",
                company or "", query,
            )
            return []

        max_retries = settings.FIRECRAWL_SEARCH_MAX_RETRIES
        for attempt in range(max_retries + 1):
            try:
                with _FIRECRAWL_SEMAPHORE:
                    with _CLOUD_SEARCH_BREAKER_LOCK:
                        paused = time.monotonic() < _CLOUD_SEARCH_PAUSED_UNTIL
                    if paused:
                        logger.info(
                            "[FIRECRAWL_SEARCH] company=%s query=%s status=skipped reason=quota_cooldown",
                            company or "", query,
                        )
                        return []
                    _wait_for_rate_slot()
                    cloud_results = self._cloud_search.search(query, limit=limit)
            except FirecrawlCloudSearchError as ex:
                if ex.reason == "http_402":
                    with _CLOUD_SEARCH_BREAKER_LOCK:
                        _CLOUD_SEARCH_PAUSED_UNTIL = max(
                            _CLOUD_SEARCH_PAUSED_UNTIL,
                            time.monotonic() + settings.FIRECRAWL_SEARCH_QUOTA_COOLDOWN_SECONDS,
                        )
                if ex.retryable and attempt < max_retries:
                    logger.info(
                        "[FIRECRAWL_SEARCH] company=%s query=%s status=retrying attempt=%d reason=%s",
                        company or "", query, attempt + 1, ex.reason,
                    )
                    continue
                logger.warning(
                    "[FIRECRAWL_SEARCH] company=%s query=%s status=failed reason=%s",
                    company or "", query, ex.reason,
                )
                return []

            results = [
                FirecrawlSearchResult(
                    url=item.url,
                    title=item.title,
                    description=item.description,
                    position=item.position,
                )
                for item in cloud_results
            ]
            logger.info(
                "[FIRECRAWL_SEARCH] company=%s query=%s status=success results=%d",
                company or "", query, len(results),
            )
            return results[:limit]
        return []


# ============================================================
# HTML LINK EXTRACTION
# ============================================================

def extract_links(
    html: str,
    base_url: str,
) -> "list[tuple[str, str]]":
    """
    Returns:

        (absolute_url, anchor_label)

    for every usable link in HTML.

    Firecrawl's `links` field does not reliably provide the
    visible anchor label, so we parse the returned HTML.
    """

    if not html:

        return []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    results = []

    for tag in soup.find_all(
        "a",
        href=True,
    ):

        href = tag[
            "href"
        ].strip()

        if not href:

            continue

        if href.startswith(
            (
                "#",
                "javascript:",
                "mailto:",
                "tel:",
            )
        ):

            continue

        absolute = urljoin(
            base_url,
            href,
        )

        label = tag.get_text(
            " ",
            strip=True,
        )

        results.append(
            (
                absolute,
                label,
            )
        )

    return results
