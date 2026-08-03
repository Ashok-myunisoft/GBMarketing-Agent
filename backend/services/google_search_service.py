"""Shared Google-search helper for GST discovery and turnover lookup.

Both flows drive the same rendered Google results page and need the same
"first organic (non-ad, non-Google) link" filtering, so it lives in one
place instead of being duplicated across the two Playwright-driven services
that use it.
"""

import logging
from urllib.parse import quote_plus, urlparse

from playwright.sync_api import BrowserContext

from services.browser_service import BrowserService

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_URL = "https://www.google.com/search?q="
# Google shows this interstitial instead of real results when it flags the
# calling IP as automated traffic. Recognising it explicitly means a blocked
# search is logged loudly as "blocked", not silently mistaken for "no results".
BLOCK_PAGE_MARKERS = ("unusual traffic", "detected unusual traffic")


class GoogleSearchService:
    """Runs a Google search in a rendered page and reads back what a user would see."""

    def __init__(self, browser: BrowserService):
        self._browser = browser

    def search_text(self, query: str, context: BrowserContext) -> str:
        """Returns the visible text of the rendered results page itself."""
        page = self._browser.new_page(context)
        try:
            self._browser.goto(page, f"{GOOGLE_SEARCH_URL}{quote_plus(query)}")
            page.wait_for_timeout(1000)
            text = page.locator("body").inner_text(timeout=5000)
            if self._is_blocked(text):
                logger.warning(
                    "Google blocked this search as automated traffic (query=%r); "
                    "no results were read. This clears on its own after a while, or "
                    "sooner from a different network/IP.",
                    query,
                )
                return ""
            return text
        except Exception as exc:
            logger.warning("Google search failed for %r: %s", query, exc)
            return ""
        finally:
            page.close()

    def organic_result_urls(self, query: str, context: BrowserContext, limit: int = 10) -> list[str]:
        """Returns organic result links, in ranking order, skipping ads and Google's own pages."""
        page = self._browser.new_page(context)
        try:
            self._browser.goto(page, f"{GOOGLE_SEARCH_URL}{quote_plus(query)}")
            page.wait_for_timeout(1000)
            urls: list[str] = []
            # Google result titles are substantially less noisy than all anchors.
            for index in range(min(page.locator("h3").count(), limit * 2)):
                h3 = page.locator("h3").nth(index)
                anchor = h3.locator("xpath=ancestor::a[1]")
                href = anchor.get_attribute("href") if anchor.count() else None
                if self._is_organic_url(href) and href not in urls:
                    urls.append(href)
                if len(urls) >= limit:
                    break
            if not urls and self._is_blocked(page.locator("body").inner_text(timeout=5000)):
                logger.warning(
                    "Google blocked this search as automated traffic (query=%r); "
                    "no results were read. This clears on its own after a while, or "
                    "sooner from a different network/IP.",
                    query,
                )
            return urls
        except Exception as exc:
            logger.warning("Google search failed for %r: %s", query, exc)
            return []
        finally:
            page.close()

    @staticmethod
    def _is_blocked(text: str) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in BLOCK_PAGE_MARKERS)

    @staticmethod
    def _is_organic_url(url) -> bool:
        if not url or not url.startswith(("http://", "https://")):
            return False
        host = urlparse(url).netloc.lower()
        return bool(host and "google." not in host)
