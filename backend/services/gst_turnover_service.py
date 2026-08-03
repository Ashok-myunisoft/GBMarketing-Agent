"""Aggregate-turnover lookup by GSTIN, read live from gst.jamku.app.

The GST portal itself never exposes an exact turnover figure to the public
(confirmed on jamku's own docs page - that's the reason "Aggregate Turnover"
is called out as special: the official portal withholds it without login).
The jamku site therefore only ever shows a turnover *slab* (e.g. "Rs. 5 Cr.
to 25 Cr."), never an exact number. Callers must treat the result as a
range, not a point value.

This drives the public site the same way a person would: search Google for
"jamku gst portal", open the first organic result, paste the GSTIN into its
search box, and click "View Aggregate Turnover" to reveal the slab. The
resolved jamku URL is cached on the instance since it's the same site for
every GSTIN - only the per-GSTIN search is repeated.
"""

import logging
import re
from typing import Optional

from playwright.sync_api import BrowserContext, Page

from services.browser_service import BrowserService
from services.google_search_service import GoogleSearchService

logger = logging.getLogger(__name__)

JAMKU_SEARCH_QUERY = "jamku gst portal"
JAMKU_SEARCH_INPUT = "#gstnosearch"
# Verified by hand as the actual top organic result for JAMKU_SEARCH_QUERY.
# Used only when that search itself returns nothing (most often because
# Google has flagged the request as automated traffic) - the destination is
# fixed, so turnover lookups shouldn't go blind just because Google did.
JAMKU_FALLBACK_URL = "https://gst.jamku.app/"


class GstTurnoverService:
    """Drives gst.jamku.app's public GSTIN search to read the Aggregate Turnover slab."""

    def __init__(self, browser: BrowserService):
        self._browser = browser
        self._google = GoogleSearchService(browser)
        self._cache: dict[str, Optional[str]] = {}
        self._jamku_url: Optional[str] = None

    def lookup(self, gstin: Optional[str]) -> Optional[str]:
        if not gstin:
            return None
        normalized = gstin.strip().upper()
        if normalized in self._cache:
            return self._cache[normalized]
        label = self._lookup_live(normalized)
        self._cache[normalized] = label
        return label

    def _lookup_live(self, gstin: str) -> Optional[str]:
        context = self._browser.new_context()
        try:
            page = self._browser.new_page(context)
            try:
                jamku_url = self._resolve_jamku_url(context)
                if not jamku_url:
                    return None
                self._browser.goto(page, jamku_url)
                page.wait_for_timeout(1200)
                search_box = page.locator(JAMKU_SEARCH_INPUT).first
                if search_box.count() == 0:
                    logger.info("jamku GST search box not found on %s", jamku_url)
                    return None
                search_box.click(force=True)
                search_box.fill(gstin, force=True)
                search_box.press("Enter")
                page.wait_for_timeout(2000)
                return self._extract_aggregate_turnover(page)
            finally:
                page.close()
        except Exception as exc:
            logger.warning("jamku turnover lookup failed for %s: %s", gstin, exc)
            return None
        finally:
            context.close()

    def _resolve_jamku_url(self, context: BrowserContext) -> Optional[str]:
        """Finds gst.jamku.app the same way a user would: search, take the first organic result."""
        if self._jamku_url:
            return self._jamku_url
        urls = self._google.organic_result_urls(JAMKU_SEARCH_QUERY, context, limit=5)
        if not urls:
            logger.info("jamku portal search returned nothing; falling back to %s", JAMKU_FALLBACK_URL)
        self._jamku_url = urls[0] if urls else JAMKU_FALLBACK_URL
        return self._jamku_url

    @staticmethod
    def _extract_aggregate_turnover(page: Page) -> Optional[str]:
        heading = page.get_by_text("Aggregate Turnover", exact=True)
        if heading.count() == 0:
            return None
        container = heading.first.locator("xpath=..")
        button = container.locator("button")
        if button.count() > 0:
            try:
                button.first.click()
                page.wait_for_timeout(1000)
            except Exception:
                pass
        value_paragraph = container.locator("p").nth(1)
        if value_paragraph.count() == 0:
            return None
        try:
            text = (value_paragraph.inner_text(timeout=3000) or "").strip()
        except Exception:
            return None
        if not text or "not available" in text.lower():
            return None
        match = re.match(r"Slab:\s*(.+?)(?:\s+FY\s*\d{4}-\d{4})?\s*$", text, re.IGNORECASE)
        label = match.group(1).strip() if match else text
        return label or None
