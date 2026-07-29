import json
import logging
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from providers.base_provider import BaseProvider
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.browser_service import BrowserService

logger = logging.getLogger(__name__)
MAX_TRADEINDIA_PAGES = 20
TRADEINDIA_SCROLL_ATTEMPTS = 8


class BusinessDirectoryProvider(BaseProvider):
    """
    Searches business directory sites for companies matching a
    SearchRequest.

    Phase 2 added IndiaMART, Phase 3 adds TradeIndia. ExportersIndia
    (Phase 4) and Justdial (Phase 5) get added the same way - a further
    per-site method merged into search(), each returning its own
    List[Company].

    IndiaMART's search page (dir.indiamart.com/search.mp) is a Next.js
    app that embeds its listing data as JSON in a `__NEXT_DATA__` script
    tag, at `props.pageProps.searchResponse`, rather than rendering
    results directly into markup present on load. As verified by hand,
    that field consistently comes back null for an automated browser
    session - the page instead renders permanent placeholder/skeleton
    listings (generic supplier text unrelated to the query) that must
    never be parsed as if they were real results. Extraction here reads
    that JSON blob rather than using CSS locators against the skeleton
    markup, and returns no results whenever it's null instead of
    fabricating companies from the placeholder content.

    TradeIndia (tradeindia.com/search.html), by contrast, has - as
    verified by hand - rendered real, query-specific listings directly
    into HTML with no blocking or placeholder substitution observed.
    Its list view doesn't expose supplier phone numbers (revealed only
    via a "View Number" interaction that leads into TradeIndia's own
    lead-capture flow, not a plain contact-info reveal) or a street
    address, only company name, city, and a link to the supplier's
    TradeIndia profile page - so `phone`, `address`, and `state` are
    left None here, same as any other field a later stage (Enrichment/
    ContactDiscovery) is meant to fill in.
    """

    INDIAMART_URL = "https://dir.indiamart.com/search.mp"
    TRADEINDIA_URL = "https://www.tradeindia.com/search.html"

    def __init__(self, browser: Optional[BrowserService] = None):
        self._browser = browser or BrowserService()

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Business Directory Provider ==========")

        owns_lifecycle = not self._browser.is_running

        if owns_lifecycle:
            self._browser.start()

        try:
            companies: List[Company] = []
            companies.extend(self._search_indiamart(request))
            companies.extend(self._search_tradeindia(request))
            return companies

        finally:
            if owns_lifecycle:
                self._browser.stop()

    # ------------------------------------------------------------------
    # IndiaMART
    # ------------------------------------------------------------------

    def _search_indiamart(self, request: SearchRequest) -> List[Company]:

        print("\n-- IndiaMART --")

        url = self._build_indiamart_url(request)

        print(f"Search URL : {url}")

        try:
            page = self._browser.new_page()

            try:
                self._browser.goto(page, url, wait_until="networkidle")
                page.wait_for_timeout(3000)

                search_response = self._extract_indiamart_search_response(page)

                if search_response is None:
                    logger.warning(
                        "IndiaMART returned placeholder content for '%s' - "
                        "no real search response available, returning no results",
                        url,
                    )
                    return []

                return self._parse_indiamart_response(search_response)

            finally:
                page.close()

        except Exception as ex:
            logger.error("IndiaMART search failed: %s", ex)
            return []

    def _build_indiamart_url(self, request: SearchRequest) -> str:

        query_parts = [
            part
            for part in [request.industry, request.location, *request.keywords]
            if part
        ]

        query = " ".join(query_parts)

        return f"{self.INDIAMART_URL}?ss={quote_plus(query)}"

    def _extract_indiamart_search_response(self, page: Page) -> Optional[dict]:
        """
        Reads the `__NEXT_DATA__` JSON blob embedded in the page and
        returns `props.pageProps.searchResponse`, or None if it isn't
        populated (i.e. the page only shows placeholder listings).
        """

        try:
            raw = page.locator("script#__NEXT_DATA__").text_content(timeout=5000)
        except PlaywrightTimeoutError:
            return None

        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None

        return data.get("props", {}).get("pageProps", {}).get("searchResponse")

    def _parse_indiamart_response(self, search_response: dict) -> List[Company]:
        """
        Converts IndiaMART's search-response JSON into Company objects.

        Not yet implemented: the real shape of `searchResponse` has
        never been observed (it has consistently been null - see class
        docstring), so there is no verified structure to map fields
        against yet. Fill this in once a real, non-null payload has
        actually been captured and inspected.
        """

        logger.warning(
            "IndiaMART returned a non-null searchResponse for the first "
            "time - _parse_indiamart_response needs real field mapping "
            "implemented against it; returning no results for now"
        )

        return []

    # ------------------------------------------------------------------
    # TradeIndia
    # ------------------------------------------------------------------

    def _search_tradeindia(self, request: SearchRequest) -> List[Company]:

        print("\n-- TradeIndia --")

        url = self._build_tradeindia_url(request)

        print(f"Search URL : {url}")

        try:
            page = self._browser.new_page()

            try:
                self._browser.goto(page, url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)

                companies = self._load_tradeindia_companies(page, max_results=request.max_results)

                print(f"TradeIndia Results : {len(companies)}")

                return companies

            finally:
                page.close()

        except Exception as ex:
            logger.error("TradeIndia search failed: %s", ex)
            return []

    def _build_tradeindia_url(self, request: SearchRequest) -> str:

        query_parts = [
            part
            for part in [request.industry, request.location, *request.keywords]
            if part
        ]

        query = " ".join(query_parts)

        return f"{self.TRADEINDIA_URL}?keyword={quote_plus(query)}"

    def _load_tradeindia_companies(self, page: Page, max_results: int) -> List[Company]:
        """Collects lazy-loaded results and advances pagination until the cap."""
        companies: List[Company] = []
        visited_urls: set[str] = set()
        for _ in range(MAX_TRADEINDIA_PAGES):
            self._scroll_tradeindia_results(page, max_results)
            remaining = max_results - len(companies)
            companies.extend(self._extract_tradeindia_companies(page, max_results=remaining))
            companies = self._deduplicate_tradeindia(companies)
            if len(companies) >= max_results or page.url in visited_urls:
                break
            visited_urls.add(page.url)
            if not self._next_tradeindia_page(page):
                break
        return companies[:max_results]

    @staticmethod
    def _scroll_tradeindia_results(page: Page, max_results: int) -> None:
        previous = 0
        for _ in range(TRADEINDIA_SCROLL_ATTEMPTS):
            current = page.locator("h3.coy-name").count()
            if current >= max_results or current <= previous and previous > 0:
                return
            previous = current
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1200)

    @staticmethod
    def _deduplicate_tradeindia(companies: List[Company]) -> List[Company]:
        by_name: dict[str, Company] = {}
        for company in companies:
            by_name.setdefault(company.company_name.strip().lower(), company)
        return list(by_name.values())

    @staticmethod
    def _next_tradeindia_page(page: Page) -> bool:
        candidates = page.locator('a[rel="next"], a:has-text("Next"), a[aria-label*="next" i]')
        for i in range(candidates.count()):
            candidate = candidates.nth(i)
            try:
                if not candidate.is_visible():
                    continue
                previous_url = page.url
                candidate.click(timeout=5000)
                page.wait_for_timeout(2500)
                if page.url != previous_url or page.locator("h3.coy-name").count() > 0:
                    return True
            except Exception:
                continue
        return False

    def _extract_tradeindia_companies(self, page: Page, max_results: int) -> List[Company]:

        seller_names = page.locator("h3.coy-name")
        count = min(seller_names.count(), max_results)

        companies: List[Company] = []

        for i in range(count):
            try:
                seller_name_el = seller_names.nth(i)

                card = seller_name_el.locator(
                    "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' card ')][1]"
                ).first

                companies.append(self._parse_tradeindia_card(card, seller_name_el))

            except Exception as ex:
                logger.warning(
                    "Skipping unparsable TradeIndia result at index %d: %s", i, ex
                )
                continue

        return companies

    def _parse_tradeindia_card(self, card: Locator, seller_name_el: Locator) -> Company:

        name = seller_name_el.inner_text().strip()

        return Company(
            company_name=name,
            website=self._extract_tradeindia_profile_url(seller_name_el),
            phone=None,
            email=None,
            address=None,
            city=self._extract_tradeindia_city(card),
            state=None,
        )

    def _extract_tradeindia_profile_url(self, seller_name_el: Locator) -> Optional[str]:
        """
        TradeIndia's list view links the seller's name to their
        marketplace profile page, not their own company domain. Company
        has no separate "platform profile URL" field, so this is stored
        in `website` as the closest available link rather than left
        empty - a later enrichment step visiting it can still discover
        the supplier's real site from there.
        """

        anchor = seller_name_el.locator("xpath=ancestor::a[1]")

        if anchor.count() == 0:
            return None

        return anchor.first.get_attribute("href")

    def _extract_tradeindia_city(self, card: Locator) -> Optional[str]:

        location = card.locator("div.d-block.mt-2")

        if location.count() == 0:
            return None

        text = location.first.inner_text().strip()

        return text or None
