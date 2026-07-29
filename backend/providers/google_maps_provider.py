import logging
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page

from providers.base_provider import BaseProvider
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.browser_service import BrowserService

logger = logging.getLogger(__name__)

RESULT_LINK_SELECTOR = "a.hfpxzc"
FEED_SELECTOR = '[role="feed"]'
NAME_SELECTOR = ".qBF1Pd"
CATEGORY_ADDRESS_SELECTOR = ".W4Efsd"
PHONE_SELECTOR = ".UsdlK"
CATEGORY_ADDRESS_SEPARATOR = " · "
HOURS_LINE_PREFIXES = ("open", "closed", "closes", "opens")
MAX_SCROLL_ATTEMPTS = 25
PLATEAU_LIMIT = 3


class GoogleMapsProvider(BaseProvider):
    """
    Searches Google Maps for companies matching a SearchRequest, reading
    directly from the search results list (no click-through into
    individual place pages, so no city/state breakdown - just the
    single-line address Maps shows in the list).

    Verified by hand: unlike Google's organic web search (which serves a
    CAPTCHA/"unusual traffic" interstitial to automated browsers) and
    IndiaMART (which serves permanent placeholder listings), Maps
    returned real, live business listings for a plain headless browser
    session with no blocking observed.

    The CSS classes below (hfpxzc, qBF1Pd, W4Efsd, UsdlK) are Google's
    own undocumented, obfuscated class names - stable enough to extract
    from today, but Google can and does change them without notice. If
    this provider suddenly starts returning nothing, check these
    selectors against a live page before assuming the query is bad.

    The results feed is scrolled (verified live: each scroll of the
    feed container loads another batch, growing from 8 to 48+ results
    across 6 scrolls in testing) until request.max_results are loaded,
    growth plateaus for a few consecutive scrolls (end of Maps' result
    set), or a safety cap on scroll attempts is hit.
    """

    def __init__(self, browser: Optional[BrowserService] = None):
        self._browser = browser or BrowserService()

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Google Maps Provider ==========")

        query = self._build_query(request)
        url = f"https://www.google.com/maps/search/{quote_plus(query)}"

        print(f"Maps Query : {query}")
        print(f"Maps URL   : {url}")

        owns_lifecycle = not self._browser.is_running

        if owns_lifecycle:
            self._browser.start()

        try:
            page = self._browser.new_page()

            try:
                self._browser.goto(page, url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)

                self._load_results(page, max_results=request.max_results)

                companies = self._extract_companies(page, max_results=request.max_results)

                print(f"Maps Results : {len(companies)}")

                return companies

            finally:
                page.close()

        except Exception as ex:
            logger.error("Google Maps search failed: %s", ex)
            return []

        finally:
            if owns_lifecycle:
                self._browser.stop()

    def _build_query(self, request: SearchRequest) -> str:

        query_parts = [
            part
            for part in [request.industry, request.location, *request.keywords]
            if part
        ]

        return " ".join(query_parts)

    def _load_results(self, page: Page, max_results: int) -> None:
        """
        Scrolls the results feed to lazy-load more listings, stopping
        once max_results are loaded, growth plateaus for PLATEAU_LIMIT
        consecutive scrolls (end of Maps' result set for this query),
        or MAX_SCROLL_ATTEMPTS is hit (safety cap on request duration).
        """

        feed = page.locator(FEED_SELECTOR)

        if feed.count() == 0:
            return

        stagnant_rounds = 0
        previous_count = page.locator(RESULT_LINK_SELECTOR).count()

        for _ in range(MAX_SCROLL_ATTEMPTS):

            if previous_count >= max_results:
                break

            feed.first.evaluate("el => el.scrollTop = el.scrollHeight")
            page.wait_for_timeout(1500)

            current_count = page.locator(RESULT_LINK_SELECTOR).count()

            if current_count <= previous_count:
                stagnant_rounds += 1
                if stagnant_rounds >= PLATEAU_LIMIT:
                    break
            else:
                stagnant_rounds = 0

            previous_count = current_count

    def _extract_companies(self, page: Page, max_results: int) -> List[Company]:

        result_links = page.locator(RESULT_LINK_SELECTOR)
        total = result_links.count()

        # Google sometimes shows the same business twice (a sponsored
        # slot plus an organic one); merge by name instead of pushing
        # that dedup work onto SearchService, keeping whichever version
        # has more data (the sponsored slot's "website" is usually just
        # an ad-click redirect, so the organic entry - if seen - wins).
        by_name: "dict[str, Company]" = {}

        for i in range(total):

            try:
                card = result_links.nth(i).locator(
                    "xpath=ancestor::div[@role='article']"
                ).first

                company = self._parse_card(card)

            except Exception as ex:
                logger.warning("Skipping unparsable Maps result at index %d: %s", i, ex)
                continue

            normalized_name = company.company_name.strip().lower()
            existing = by_name.get(normalized_name)

            if existing is None or (existing.website is None and company.website is not None):
                by_name[normalized_name] = company

        return list(by_name.values())[:max_results]

    def _parse_card(self, card: Locator) -> Company:

        name = card.locator(NAME_SELECTOR).first.inner_text().strip()

        return Company(
            company_name=name,
            website=self._extract_website(card),
            phone=self._extract_phone(card),
            email=None,
            address=self._extract_address(card),
            city=None,
            state=None,
        )

    def _extract_website(self, card: Locator) -> Optional[str]:

        website_link = card.locator('a[aria-label*="website"]')

        if website_link.count() == 0:
            return None

        href = website_link.first.get_attribute("href")

        # Sponsored result slots reuse the same "website" link pattern
        # but point at a Google ad-click redirect, not the real site.
        if not href or href.startswith("/") or "google.com/aclk" in href:
            return None

        return href

    def _extract_phone(self, card: Locator) -> Optional[str]:

        phone_span = card.locator(PHONE_SELECTOR)

        if phone_span.count() == 0:
            return None

        return phone_span.first.inner_text().strip()

    def _extract_address(self, card: Locator) -> Optional[str]:
        """
        Maps renders this as a "category · address" line, but sometimes
        splices in an extra icon glyph as its own invisible segment
        (e.g. "Wholesaler ·  · 42 Main St"). Segments are filtered down
        to ones with actual alphanumeric content before picking the
        last one as the address, rather than trusting a fixed position.

        The combined preview blob (category+address+hours+phone as one
        multi-line string) and the standalone "Open · Closes 8pm ·
        <phone>" line both use the same separator, so multi-line
        candidates and ones starting with an hours-status word are
        skipped to avoid returning a phone number as the address.
        """

        lines = card.locator(CATEGORY_ADDRESS_SELECTOR)

        for i in range(lines.count()):
            text = lines.nth(i).inner_text()

            if "\n" in text or CATEGORY_ADDRESS_SEPARATOR not in text:
                continue

            segments = [
                segment.strip()
                for segment in text.split(CATEGORY_ADDRESS_SEPARATOR)
                if any(ch.isalnum() for ch in segment)
            ]

            if len(segments) < 2:
                continue

            if segments[0].lower().startswith(HOURS_LINE_PREFIXES):
                continue

            return segments[-1]

        return None
