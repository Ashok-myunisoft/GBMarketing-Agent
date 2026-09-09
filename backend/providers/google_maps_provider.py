import logging
from typing import List, Optional
from urllib.parse import quote_plus

from playwright.sync_api import Locator, Page

from config.geography import classify_location
from providers.base_provider import BaseProvider
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.browser_service import BrowserService
from services.geocoding_service import GeoapifyGeocodingService, GeocodedAddress

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

    def __init__(self, browser: Optional[BrowserService] = None, geocoder: Optional[GeoapifyGeocodingService] = None):
        self._browser = browser or BrowserService()
        # Reused across every search() call on this instance, so geocoding
        # the same location string across ~9-16 industry sub-queries for
        # one search (see services/search_services.py) only ever costs one
        # real network call - GeoapifyGeocodingService caches by string.
        self._geocoder = geocoder or GeoapifyGeocodingService()

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

                # Geocoded once per distinct location string (cached across
                # every industry sub-query for it - see __init__) so
                # _is_within_requested_location can reject a result at full
                # district/city precision even for a requested city outside
                # the small hand-picked CITY_ALIASES/CITY_DISTRICTS seed
                # lists, not just the cities already hand-added there.
                requested_geocoded = (
                    self._geocoder.geocode(request.location) if request.location else None
                )

                companies = self._extract_companies(
                    page, max_results=request.max_results, requested_location=request.location,
                    requested_geocoded=requested_geocoded,
                )

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

    def _extract_companies(
        self, page: Page, max_results: int, requested_location: Optional[str] = None,
        requested_geocoded: Optional[GeocodedAddress] = None,
    ) -> List[Company]:

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

            if not self._is_within_requested_location(company, requested_location, requested_geocoded):
                continue

            normalized_name = company.company_name.strip().lower()
            existing = by_name.get(normalized_name)

            if existing is None or (existing.website is None and company.website is not None):
                by_name[normalized_name] = company

        return list(by_name.values())[:max_results]

    @staticmethod
    def _is_within_requested_location(
        company: Company, requested_location: Optional[str],
        requested_geocoded: Optional[GeocodedAddress] = None,
    ) -> bool:
        """Drops a result only on confirmed conflicting evidence.

        Maps itself decides how far to search, and pads a sparse local
        result set with listings from a wider area (verified by hand: a
        niche category query for one city can surface a result whose
        address plainly names a different city). Nothing upstream
        constrains that, so this checks each result here using the same
        address-based evidence ValidationAgent's classify_location already
        trusts - it only ever discards on a *confirmed different* known
        city/locality, never merely because the address is missing or
        doesn't mention the requested place (that stays "unknown", kept,
        and left for ValidationAgent to flag as unverified).

        ``requested_geocoded`` - the caller's one-time geocoding of
        ``requested_location`` - lets this reject at full district/city
        precision even for a requested city outside the small hand-picked
        CITY_ALIASES/CITY_DISTRICTS seed lists (e.g. "Nagpur").
        """
        if not requested_location:
            return True
        decision, reason = classify_location(
            None, None, company.address, requested_location,
            requested_geocoded_state=requested_geocoded.state if requested_geocoded else None,
            requested_geocoded_district=requested_geocoded.district if requested_geocoded else None,
            requested_geocoded_city=requested_geocoded.city if requested_geocoded else None,
        )
        if decision == "outside":
            logger.info(
                "Dropping Maps result '%s' outside requested '%s': %s",
                company.company_name, requested_location, reason,
            )
            return False
        return True

    def _parse_card(self, card: Locator) -> Company:

        name = card.locator(NAME_SELECTOR).first.inner_text().strip()
        category, address = self._extract_category_and_address(card)

        return Company(
            company_name=name,
            website=self._extract_website(card),
            phone=self._extract_phone(card),
            email=None,
            address=address,
            city=None,
            state=None,
            # The category Maps assigns each listing (e.g. "Pump supplier")
            # is the only real, observed per-company industry signal any
            # provider exposes - unlike the search term used to find it, this
            # actually reflects what the business is, so ValidationAgent can
            # check it for real instead of rubber-stamping the query itself.
            industry=category,
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

    def _extract_category_and_address(self, card: Locator) -> "tuple[Optional[str], Optional[str]]":
        """
        Maps renders this as a "category · address" line, but sometimes
        splices in an extra icon glyph as its own invisible segment
        (e.g. "Wholesaler ·  · 42 Main St"). Segments are filtered down
        to ones with actual alphanumeric content before picking the
        first as the category and the last as the address, rather than
        trusting a fixed position.

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

            return segments[0], segments[-1]

        return None, None
