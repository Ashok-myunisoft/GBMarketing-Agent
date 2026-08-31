import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import List, Optional
from urllib.parse import quote_plus, urljoin, urlparse

from playwright.sync_api import BrowserContext, Page

from agents.base_agent import BaseClass
from config.geography import parse_address_components
from core.config import settings
from schemas.company import Company
from services.browser_service import BrowserService
from services.contact_extraction.designation_rules import canonical_designation
from services.contact_extraction.name_rules import is_person_name, normalize_name
from services.contact_extraction.page_classifier import classify as classify_page
from services.contact_extraction.pipeline import ContactExtractionPipeline
from services.geocoding_service import GeoapifyGeocodingService
from services.filesure_service import FileSureService
from services.gst_turnover_service import GstTurnoverService
from services.gst_turnover_enrichment.service import GstTurnoverEnrichmentService
from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient
from services.gst_turnover_enrichment.gst_extraction import find_valid_gstin
from services.enrichment.company_enrichment import CompanyTavilyEnrichmentService

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_PATTERN = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")
CIN_PATTERN = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", re.IGNORECASE)
PINCODE_PATTERN = re.compile(r"\b\d{6}\b")
ADDRESS_HINT_WORDS = (
    "road", "street", "nagar", "estate", "industrial", "layout", "floor",
    "building", "complex", "colony", "phase", "block", "marg", "sector",
)
@contextmanager
def _timed(company_name: str, stage: str):
    """Logs how long one enrichment stage took for one company, at INFO
    level, so a slow run can be diagnosed from logs alone (which stage is
    actually eating the time: browser I/O vs LLM calls vs a specific
    third-party lookup) without attaching a profiler."""
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        logger.info("[PERF] %s: %d ms company=%s", stage, round(elapsed * 1000), company_name)


CONTACT_LINK_TEXT = "Contact"
CONTACT_PAGE_LINK_TEXT = (
    "contact", "about", "team", "management", "leadership", "legal",
    "privacy", "terms", "gst", "company-info",
)
TOFLER_URL = "https://www.tofler.in/"
TOFLER_SEARCH_INPUT = 'input[placeholder="Search company, CIN OR DIN"]'
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_PEOPLE_SEARCH_URL = "https://www.linkedin.com/search/results/people/?keywords="


class EnrichmentAgent(BaseClass):
    """
    Fills in email and a fuller street address than search providers
    surface, from two sources in order:

    1. The company's own website (mailto: links, plus a 6-digit Indian
       PIN-code heuristic for the address). Real company sites vary
       enormously in structure (unlike a single directory's consistent
       markup), so this is inherently best-effort - verified by hand
       against several live company sites before writing this, coverage
       will never be 100%.
    2. Tofler's free company-registry lookup (by company name), used
       only for whatever the website didn't provide, or when there's no
       website at all. Tofler's actual turnover/revenue/employee-count
       figures are paywalled behind "GET PRO" - confirmed by hand on a
       real company profile page - so this never attempts to read
       those, only the freely visible registered office address and
       registered company email.

    Companies where neither source yields anything, or where lookups
    fail outright, are returned unchanged rather than dropped from the
    list.

    Also attempts to find a named individual contact (name, designation
    matched against the Target Designation taxonomy, LinkedIn profile
    link) on the same website visit. Verified by hand across 6 live
    target companies (including a multinational, hawle.com): none
    published a named individual anywhere on their public site -
    manufacturing/industrial B2B sites in this vertical overwhelmingly
    gate contact behind a generic enquiry form or company-level phone/
    email, not a named person. This will therefore return no contact
    for the large majority of companies - that reflects what's actually
    publicly available, not a bug - and exists to catch the rare
    exception without a second page-load pass.
    """

    def __init__(
        self,
        browser: Optional[BrowserService] = None,
        geocoder: Optional[GeoapifyGeocodingService] = None,
        filesure: Optional[FileSureService] = None,
        turnover: Optional[GstTurnoverService] = None,
        tavily_enrichment: Optional[CompanyTavilyEnrichmentService] = None,
        firecrawl: Optional[FirecrawlClient] = None,
    ):
        # Enrichment is best-effort: a single unresponsive company site must
        # not hold up an entire batch for the browser's general 30-second
        # timeout and repeated retries.
        self._browser = browser or BrowserService(
            timeout_ms=settings.ENRICHMENT_PLAYWRIGHT_TIMEOUT_MS,
            max_retries=1,
        )
        self._geocoder = geocoder or GeoapifyGeocodingService()
        self._filesure = filesure or FileSureService()
        # Retain the historical injection point without constructing legacy
        # search services. Production resolution is self._gst_turnover.
        self._turnover = turnover
        # Plain HTTPS API, no Playwright involved - the sole content-fetch
        # mechanism for GST/turnover (see services/gst_turnover_enrichment).
        self._firecrawl = firecrawl or FirecrawlClient()
        self._gst_turnover = GstTurnoverEnrichmentService(self._browser, self._firecrawl)
        # Tavily search + HTML/PDF crawl + LLM structured extraction (see
        # services/enrichment/company_enrichment.py). A no-op whenever
        # TAVILY_API_KEY isn't configured, so this is safe to always
        # construct - existing behaviour is unchanged until a key is set.
        # Shares this agent's own FirecrawlClient (same global cache/rate
        # limiter/concurrency slots) instead of constructing a second one,
        # so PDF enrichment in the Tavily pass and GST/turnover enrichment
        # aren't independently rate-limited against the same Firecrawl
        # deployment.
        self._tavily = tavily_enrichment or CompanyTavilyEnrichmentService(self._browser, firecrawl=self._firecrawl)
        self._linkedin_context: Optional[BrowserContext] = None
        self._linkedin_authenticated = False
        self._linkedin_unavailable = False
        # Small dedicated pool for the FileSure prefetch (plain HTTPS calls,
        # no Playwright involved) - separate from the per-company worker
        # pool in execute() so it doesn't compete with those for slots.
        self._filesure_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="filesure-prefetch"
        )

    def execute(self, companies: List[Company]) -> List[Company]:

        print("\n========== Enrichment Agent Started ==========")

        # Playwright's synchronous API is thread-affine, so every worker owns
        # its own agent/browser rather than sharing this agent's browser across
        # threads.  This preserves the same extraction rules while overlapping
        # slow remote requests from different companies.
        worker_count = min(settings.ENRICHMENT_CONCURRENCY, len(companies)) if companies else 0
        if worker_count <= 1:
            enriched = self._enrich_batch(companies)
        else:
            batches = [companies[index::worker_count] for index in range(worker_count)]
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="enrichment") as executor:
                results = list(executor.map(self._enrich_batch_in_isolated_agent, batches))
            # Restore the search result's original ordering after round-robin
            # batching, so downstream validation/export behaviour is stable.
            enriched = [company for offset in range(max(map(len, results))) for batch in results for company in batch[offset:offset + 1]]

        found_email = sum(1 for c in enriched if c.email)
        found_address = sum(1 for c in enriched if c.address)
        found_contact = sum(1 for c in enriched if c.contact_person)
        found_alt_phone = sum(1 for c in enriched if c.phone_alt)
        found_gst = sum(1 for c in enriched if c.gst)
        found_turnover = sum(1 for c in enriched if c.turnover)
        found_city = sum(1 for c in enriched if c.city)
        found_region = sum(1 for c in enriched if c.region)

        print(
            f"Enriched {found_email}/{len(enriched)} with email, "
            f"{found_address}/{len(enriched)} with address, "
            f"{found_contact}/{len(enriched)} with a named contact, "
            f"{found_alt_phone}/{len(enriched)} with an alternate phone, "
            f"{found_gst}/{len(enriched)} with GST, {found_turnover}/{len(enriched)} with turnover, "
            f"{found_city}/{len(enriched)} with city, {found_region}/{len(enriched)} with region"
        )
        print("========== Enrichment Agent Completed ==========")

        return enriched

    def _enrich_batch(self, companies: List[Company]) -> List[Company]:
        """Enrich one worker's companies with one browser lifecycle."""
        owns_lifecycle = not self._browser.is_running
        if owns_lifecycle:
            self._browser.start()
        try:
            return [self._enrich(company) for company in companies]
        finally:
            if self._linkedin_context is not None:
                self._linkedin_context.close()
                self._linkedin_context = None
            if self._turnover is not None:
                self._turnover.close()
            self._filesure_executor.shutdown(wait=False, cancel_futures=True)
            if owns_lifecycle:
                self._browser.stop()

    @staticmethod
    def _enrich_batch_in_isolated_agent(companies: List[Company]) -> List[Company]:
        """Run a batch in a browser that belongs to this worker thread."""
        return EnrichmentAgent()._enrich_batch(companies)

    def _enrich(self, company: Company) -> Company:
        """Measure the complete job even when a best-effort source fails."""
        started = time.monotonic()
        try:
            try:
                return self._enrich_impl(company)
            except Exception:
                # Every individual source (website, Tofler, LinkedIn, GST/turnover,
                # LLM extraction) already degrades gracefully on its own failure -
                # this is the backstop for anything else (a schema edge case, an
                # unhandled library exception) that would otherwise abort this
                # worker's entire remaining batch instead of just this company.
                logger.exception(
                    "Unhandled enrichment failure for '%s' - keeping company unchanged",
                    company.company_name,
                )
                return company
        finally:
            logger.info(
                "[PERF] total enrichment: %d ms company=%s",
                round((time.monotonic() - started) * 1000), company.company_name,
            )

    def _enrich_impl(self, company: Company) -> Company:

        email = None
        address = None
        contact_candidates: list = []
        phones: list[str] = []
        cin = company.cin
        official_name: Optional[str] = None
        official_url: Optional[str] = None

        contact_pipeline = ContactExtractionPipeline(company.company_name, company.website)

        # FileSure is a plain HTTPS call (no Playwright involved), so when a
        # CIN is already present on the input record it can run on a
        # background thread for the several-second duration of the browser-
        # bound Tavily/website work below instead of waiting until after it.
        # Only usable when the CIN is already known here - a CIN discovered
        # later, during the website crawl, still has to wait and is looked
        # up synchronously further down as before.
        filesure_future = None
        if cin:
            filesure_future = self._filesure_executor.submit(self._filesure.lookup, cin)

        # Tavily search + HTML/PDF crawl + one LLM structured-extraction call
        # (see services/enrichment/company_enrichment.py). A no-op returning
        # an empty result whenever TAVILY_API_KEY isn't configured. Its
        # output is never applied directly - it only feeds candidates into
        # the same GST/turnover/contact scoring machinery below, and fills
        # whatever the website crawl/Tofler/FileSure fallbacks still miss.
        with _timed(company.company_name, "tavily.gather"):
            tavily_result = self._tavily.gather(company.company_name, company.website)

        if company.website and company.website.startswith("http"):
            with _timed(company.company_name, "website crawl (email/address/contact)"):
                (
                    email, address, contact_candidates,
                    website_cin, website_phones, official_name, official_url,
                ) = self._enrich_from_website(company, contact_pipeline)
            cin = cin or website_cin
            phones.extend(website_phones)

        email = email or tavily_result.email
        address = address or tavily_result.address
        official_url = official_url or tavily_result.website

        # GST Number and Turnover: the company's own website via Firecrawl,
        # followed by the jamku turnover-slab fallback. No Tavily, no Google
        # search is used. A value already on the record (from a prior
        # run/import) is used as-is; the multi-tier lookup only runs for
        # whatever is still missing.
        gst = find_valid_gstin(company.gst) if company.gst else None
        turnover = company.turnover
        gst_blocked = False
        gst_turnover_result = None
        if not gst or (settings.ENRICHMENT_LOOKUP_TURNOVER and not turnover):
            with _timed(company.company_name, "gst_turnover.resolve"):
                gst_turnover_result = self._gst_turnover.resolve(
                    official_name or company.company_name,
                    # Kept for the existing resolver signature only.  The
                    # Firecrawl Search GST/turnover path deliberately never
                    # uses this website value as an input.
                    official_url or company.website,
                    gst=gst,
                    city=company.city,
                    state=company.state,
                    industry=company.industry,
                )
            gst = gst or (gst_turnover_result.gst.value or None)
            turnover = turnover or (gst_turnover_result.turnover.value or None)

        if tavily_result.contact_person:
            tavily_candidate = contact_pipeline.external_candidate(
                tavily_result.contact_person,
                tavily_result.designation,
                "tavily",
                source_url=tavily_result.contact_source_url or "tavily",
            )
            if tavily_candidate:
                contact_candidates.append(tavily_candidate)

        if email is None or address is None:
            with _timed(company.company_name, "tofler lookup"):
                tofler_address, tofler_email = self._lookup_tofler(official_name or company.company_name)
            email = email or tofler_email
            address = address or tofler_address

        need_filesure = cin and (address is None or gst is None or not contact_candidates)
        if need_filesure and filesure_future is not None:
            # Started in the background near the top of this method - by now
            # the Tavily/website/GST work above has very likely already
            # covered its wait time, so this is usually an instant .result().
            with _timed(company.company_name, "filesure (prefetched, awaiting)"):
                filesure_data = filesure_future.result()
        elif need_filesure:
            # cin only became known partway through this method (e.g. found
            # during the website crawl), so there was nothing to prefetch.
            with _timed(company.company_name, "filesure (cold lookup)"):
                filesure_data = self._filesure.lookup(cin)
        else:
            filesure_data = None
            if filesure_future is not None:
                filesure_future.cancel()
        if filesure_data:
            address = address or filesure_data.address
            gst = gst or filesure_data.gst
            # Wrapped as a candidate rather than a blind fallback fill, so a
            # website hit and an MCA/FileSure hit for the same person raise
            # each other's confidence instead of the first source found
            # winning outright (Step 7 cross-source validation).
            filesure_candidate = contact_pipeline.external_candidate(
                filesure_data.contact_person,
                filesure_data.designation,
                "filesure",
                source_url=f"filesure:{cin}",
            )
            if filesure_candidate:
                contact_candidates.append(filesure_candidate)

        # LinkedIn is a last resort: company websites and public registries
        # remain the preferred sources for named contacts.
        if settings.ENRICHMENT_LOOKUP_LINKEDIN and not contact_candidates:
            with _timed(company.company_name, "linkedin lookup"):
                linkedin_contact, linkedin_designation, linkedin_profile = self._lookup_linkedin_contact(company)
            linkedin_candidate = contact_pipeline.external_candidate(
                linkedin_contact,
                linkedin_designation,
                "linkedin",
                source_url=linkedin_profile or "linkedin",
                linkedin_url=linkedin_profile,
            )
            if linkedin_candidate:
                contact_candidates.append(linkedin_candidate)

        with _timed(company.company_name, "validation"):
            contact_result = contact_pipeline.select_best(contact_candidates)
        contact_person = contact_result.contact_person if contact_result else None
        designation = contact_result.designation if contact_result else None
        linkedin_url = contact_result.linkedin_url if contact_result else None

        # Merge website-discovered numbers with the one Maps/directory
        # search already found, keeping order and dropping duplicates,
        # so the primary "phone" never changes but a second distinct
        # number lands in "phone_alt".
        existing_digits = self._digits(company.phone)
        phone = company.phone
        phone_alt = company.phone_alt
        for candidate in phones:
            digits = self._digits(candidate)
            if not digits or digits == existing_digits:
                continue
            if phone is None:
                phone = candidate
                existing_digits = digits
            elif phone_alt is None and digits != self._digits(phone):
                phone_alt = candidate
                break

        city, state = parse_address_components(address or company.address)
        city = city or tavily_result.city
        state = state or tavily_result.state
        if not (city and state):
            with _timed(company.company_name, "geocoding"):
                geocoded = self._geocoder.geocode(address or company.address)
        else:
            geocoded = None
        if geocoded:
            city = geocoded.city or city
            state = geocoded.state or state
        # Region is the company's state, not a Geoapify ward/suburb - those
        # are far too granular for the "Region" column's intended meaning.
        region = state

        industry = company.industry or tavily_result.industry
        country = company.country or tavily_result.country
        pincode = company.pincode or tavily_result.pincode
        business_category = company.business_category or tavily_result.business_category
        field_confidence = tavily_result.field_confidence or company.field_confidence
        field_evidence = tavily_result.field_evidence or company.field_evidence
        field_sources = tavily_result.field_sources or company.field_sources
        field_status = dict(company.field_status)
        if gst_turnover_result:
            field_confidence = dict(field_confidence)
            field_sources = dict(field_sources)
            field_evidence = dict(field_evidence)
            field_confidence.update({"gst": gst_turnover_result.gst.confidence, "turnover": gst_turnover_result.turnover.confidence})
            field_sources.update({"gst": gst_turnover_result.gst.source_url or "", "turnover": gst_turnover_result.turnover.source_url or ""})
            field_status.update({"gst": gst_turnover_result.gst.status, "turnover": gst_turnover_result.turnover.status})
            field_evidence.update({
                "turnover_financial_year": gst_turnover_result.turnover.financial_year or "",
                "turnover_metric": gst_turnover_result.turnover.metric or "",
                "gst_source_type": gst_turnover_result.gst.source_type or "",
                "turnover_source_type": gst_turnover_result.turnover.source_type or "",
            })
        field_status["contact"] = "verified" if contact_result and contact_result.confidence >= 90 else ("probable" if contact_result else "needs_verification")

        remark = None

        if all(
            v is None
            for v in (email, address, contact_person, designation, linkedin_url, gst, cin, turnover)
        ) and phone_alt is None and official_name is None and remark is None and tavily_result.is_empty():
            return company

        enriched = company.model_copy(
            update={
                "email": email or company.email,
                "company_name": official_name or company.company_name,
                "website": official_url or company.website,
                "address": address or company.address,
                "contact_person": contact_person or company.contact_person,
                "designation": designation or company.designation,
                "linkedin_url": linkedin_url or company.linkedin_url,
                "phone": phone,
                "phone_alt": phone_alt,
                "gst": gst,
                "cin": cin,
                "turnover": turnover,
                "city": city or company.city,
                "state": state or company.state,
                "region": region or company.region,
                "remarks": remark or company.remarks,
                "industry": industry,
                "country": country,
                "pincode": pincode,
                "business_category": business_category,
                "field_confidence": field_confidence,
                "field_evidence": field_evidence,
                "field_sources": field_sources,
                "field_status": field_status,
                "turnover_financial_year": gst_turnover_result.turnover.financial_year if gst_turnover_result else company.turnover_financial_year,
                "turnover_metric": gst_turnover_result.turnover.metric if gst_turnover_result else company.turnover_metric,
            }
        )
        return enriched

    def _lookup_linkedin_contact(
        self, company: Company
    ) -> "tuple[Optional[str], Optional[str], Optional[str]]":
        """Finds one target-role contact from LinkedIn, without bypassing challenges."""
        if self._linkedin_unavailable:
            return None, None, None
        if not settings.LINKEDIN_EMAIL or not settings.LINKEDIN_PASSWORD:
            logger.info("LinkedIn contact lookup skipped: LINKEDIN_EMAIL/PASSWORD are not configured")
            self._linkedin_unavailable = True
            return None, None, None

        try:
            context = self._linkedin_session()
            if context is None:
                return None, None, None
            page = self._browser.new_page(context)
            try:
                keywords = company.company_name
                if company.city:
                    keywords = f"{keywords} {company.city}"
                self._browser.goto(page, f"{LINKEDIN_PEOPLE_SEARCH_URL}{quote_plus(keywords)}")
                page.wait_for_timeout(1500)
                return self._extract_linkedin_search_contact(page, company.company_name)
            finally:
                page.close()
        except Exception as ex:
            logger.warning("LinkedIn contact lookup failed for '%s': %s", company.company_name, ex)
            return None, None, None

    def _linkedin_session(self) -> Optional[BrowserContext]:
        if self._linkedin_authenticated and self._linkedin_context is not None:
            return self._linkedin_context

        context = self._browser.new_context()
        page = self._browser.new_page(context)
        try:
            self._browser.goto(page, LINKEDIN_LOGIN_URL)
            page.locator('input[name="session_key"], input#username').first.fill(settings.LINKEDIN_EMAIL)
            page.locator('input[name="session_password"], input#password').first.fill(settings.LINKEDIN_PASSWORD)
            page.locator('button[type="submit"]').first.click()
            page.wait_for_timeout(2000)

            # CAPTCHA, security-verification, and MFA flows require the account
            # owner to complete them interactively; this code never bypasses them.
            blocked = any(token in page.url.lower() for token in ("checkpoint", "challenge", "captcha"))
            if blocked or "login" in page.url.lower():
                logger.warning("LinkedIn login needs interactive verification; contact lookup is skipped")
                self._linkedin_unavailable = True
                context.close()
                return None

            self._linkedin_context = context
            self._linkedin_authenticated = True
            return context
        except Exception:
            context.close()
            raise
        finally:
            page.close()

    def _extract_linkedin_search_contact(
        self, page: Page, company_name: str
    ) -> "tuple[Optional[str], Optional[str], Optional[str]]":
        """Extracts one verifiable name, target designation, and profile URL."""
        company_terms = {
            token.lower() for token in re.findall(r"[A-Za-z0-9]+", company_name)
            if len(token) > 2 and token.lower() not in {"private", "limited", "ltd", "pvt", "llp"}
        }
        cards = page.locator("li")
        for index in range(min(cards.count(), 30)):
            card = cards.nth(index)
            try:
                text = (card.inner_text(timeout=1000) or "").strip()
                if not text:
                    continue
                lowered = text.lower()
                if company_terms and not any(term in lowered for term in company_terms):
                    continue
                designation = canonical_designation(text)
                profile = card.locator('a[href*="/in/"]').first
                if not designation or profile.count() == 0:
                    continue
                name = (profile.inner_text(timeout=1000) or "").strip().splitlines()[0]
                if is_person_name(name):
                    href = profile.get_attribute("href") or ""
                    return normalize_name(name), designation, href.split("?")[0]
            except Exception:
                continue
        return None, None, None

    @staticmethod
    def _digits(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        digits = re.sub(r"\D+", "", value)
        return digits[-10:] if len(digits) >= 10 else digits or None

    def _enrich_from_website(
        self, company: Company, contact_pipeline: ContactExtractionPipeline
    ) -> "tuple[Optional[str], Optional[str], list, Optional[str], list[str], Optional[str], Optional[str]]":
        """Returns email, address, contact candidates, CIN, phone numbers, and official name/URL found.

        GST is intentionally not extracted here - GstTurnoverEnrichmentService
        (see services/gst_turnover_enrichment) now owns GST/turnover discovery
        end to end with its own dedicated website visit, so this method's
        page-selection/early-exit rules (tuned for email/address/contact) are
        never affected by GST/turnover-specific changes.
        """

        try:
            context = self._browser.new_context()
            page = self._browser.new_page(context)

            try:
                self._browser.goto(page, company.website)
                page.wait_for_timeout(1500)

                official_name = self._extract_official_company_name(page)
                official_url = page.url
                email = self._extract_email(page)
                address = self._extract_address(page)
                contact_candidates = contact_pipeline.extract_from_page(page, page.url, classify_page(page.url))
                cin = self._extract_cin(page)
                phones = self._extract_phones(page)

                supplemental_urls = self._supplemental_urls(page)
                for supplemental_url in supplemental_urls:
                    try:
                        self._browser.goto(page, supplemental_url)
                        page.wait_for_timeout(1000)
                    except Exception:
                        continue
                    email = email or self._extract_email(page)
                    address = address or self._extract_address(page)
                    cin = cin or self._extract_cin(page)
                    # Every visited page's candidates are kept, never just the
                    # first page that had one (Step 3): a leadership page
                    # visited later can still outrank a weaker contact-page
                    # hit found earlier, once scored.
                    contact_candidates.extend(
                        contact_pipeline.extract_from_page(page, page.url, classify_page(page.url))
                    )
                    if len(phones) < 2:
                        phones = list(dict.fromkeys(phones + self._extract_phones(page)))

                    # A named person is uncommon on industrial sites; do not
                    # make their absence force visits to every link.
                    if email and address and len(phones) >= 2:
                        break

                return (
                    email, address, contact_candidates, cin, phones,
                    official_name, official_url,
                )

            finally:
                page.close()
                context.close()

        except Exception as ex:
            logger.warning(
                "Website enrichment failed for '%s' (%s): %s",
                company.company_name,
                company.website,
                ex,
            )
            return None, None, [], None, [], None, None

    @staticmethod
    def _extract_official_company_name(page: Page) -> Optional[str]:
        """Prefer legal/organisation metadata exposed by the official site."""
        try:
            candidates: list[tuple[int, str]] = []
            structured = page.locator('script[type="application/ld+json"]')
            for index in range(structured.count()):
                raw = structured.nth(index).inner_text(timeout=1000)
                try:
                    EnrichmentAgent._collect_jsonld_names(json.loads(raw), candidates)
                except (TypeError, ValueError):
                    continue
            if candidates:
                # Pages like a marketplace seller-profile commonly carry both
                # the marketplace's own sitewide Organization schema *and* a
                # LocalBusiness schema for the specific seller - picking
                # whichever JSON-LD block merely comes first on the page
                # would silently return the marketplace's own name instead
                # of the company's. Highest specificity wins regardless of
                # position; ties keep first-seen order (stable sort).
                candidates.sort(key=lambda item: item[0], reverse=True)
                return candidates[0][1]
            for selector in ('meta[property="og:site_name"]', 'meta[name="application-name"]'):
                value = page.locator(selector).first.get_attribute("content")
                if value and EnrichmentAgent._looks_like_company_name(value):
                    return re.sub(r"\s+", " ", value).strip()
            h1 = page.locator("h1").first.inner_text(timeout=1000)
            if h1 and EnrichmentAgent._looks_like_company_name(h1):
                return re.sub(r"\s+", " ", h1).strip()
        except Exception:
            pass
        return None

    # Higher specificity wins when a page carries more than one JSON-LD
    # name candidate. An explicit legalName is the most authoritative
    # source there is; a bare "Organization" node is often sitewide
    # publisher/platform boilerplate rather than the entity the page is
    # actually about, so it ranks below the more specific business types.
    _JSONLD_TYPE_SPECIFICITY = {"organization": 0, "corporation": 1, "localbusiness": 2}
    _JSONLD_LEGAL_NAME_RANK = 3

    @staticmethod
    def _collect_jsonld_names(value, candidates: "list[tuple[int, str]]") -> None:
        """Appends every (specificity, name) candidate found, without mistaking a product for one."""
        if isinstance(value, list):
            for item in value:
                EnrichmentAgent._collect_jsonld_names(item, candidates)
            return
        if not isinstance(value, dict):
            return
        legal_name = value.get("legalName")
        if isinstance(legal_name, str) and EnrichmentAgent._looks_like_company_name(legal_name):
            candidates.append((EnrichmentAgent._JSONLD_LEGAL_NAME_RANK, re.sub(r"\s+", " ", legal_name).strip()))
        kind = value.get("@type", "")
        kinds = {kind.lower()} if isinstance(kind, str) else {str(item).lower() for item in kind}
        matched_kinds = EnrichmentAgent._JSONLD_TYPE_SPECIFICITY.keys() & kinds
        name = value.get("name")
        if matched_kinds and isinstance(name, str) and EnrichmentAgent._looks_like_company_name(name):
            specificity = max(EnrichmentAgent._JSONLD_TYPE_SPECIFICITY[kind] for kind in matched_kinds)
            candidates.append((specificity, re.sub(r"\s+", " ", name).strip()))
        graph = value.get("@graph")
        if graph:
            EnrichmentAgent._collect_jsonld_names(graph, candidates)

    @staticmethod
    def _looks_like_company_name(value: str) -> bool:
        normalized = re.sub(r"\s+", " ", value or "").strip()
        if not 2 <= len(normalized) <= 160 or "@" in normalized or "http" in normalized.lower():
            return False
        words = normalized.split()
        return len(words) <= 16 and sum(char.isalpha() for char in normalized) >= 3

    def _lookup_tofler(self, company_name: str) -> "tuple[Optional[str], Optional[str]]":
        """Returns (address, email) from Tofler's free registered-details lookup, or (None, None)."""

        try:
            context = self._browser.new_context()
            page = self._browser.new_page(context)

            try:
                self._browser.goto(page, TOFLER_URL)
                page.wait_for_timeout(1500)

                search_box = self._find_visible_locator(page, TOFLER_SEARCH_INPUT)

                if search_box is None:
                    return None, None

                search_box.click(force=True)
                search_box.fill(company_name, force=True)
                page.wait_for_timeout(2000)

                suggestions = page.locator('li:has-text("Active"), li:has-text("Inactive")')

                if suggestions.count() == 0:
                    return None, None

                suggestions.first.click()
                page.wait_for_timeout(2500)

                return self._extract_tofler_address(page), self._extract_tofler_email(page)

            finally:
                page.close()
                context.close()

        except Exception as ex:
            logger.warning("Tofler lookup failed for '%s': %s", company_name, ex)
            return None, None

    def _find_visible_locator(self, page: Page, selector: str):

        candidates = page.locator(selector)

        for i in range(candidates.count()):
            candidate = candidates.nth(i)
            if candidate.is_visible():
                return candidate

        return None

    def _extract_tofler_address(self, page: Page) -> Optional[str]:

        address_row = page.locator('tr:has-text("Registered Office")')

        if address_row.count() == 0:
            return None

        cells = address_row.first.locator("td")

        if cells.count() < 3:
            return None

        return cells.nth(2).inner_text().strip() or None

    def _extract_tofler_email(self, page: Page) -> Optional[str]:

        email_label = page.locator(':text("Company Email")')

        if email_label.count() == 0:
            return None

        parent_text = email_label.first.locator("xpath=..").inner_text()
        lines = [line.strip() for line in parent_text.splitlines() if line.strip()]

        if len(lines) >= 2 and "@" in lines[-1]:
            return lines[-1]

        return None

    def _visit_contact_page(self, page: Page) -> None:
        """Follows a likely contact/about/team link, best-effort."""

        links = page.locator("a[href]")
        for i in range(links.count()):
            link = links.nth(i)
            try:
                label = (link.inner_text(timeout=1000) or "").strip().lower()
                href = (link.get_attribute("href") or "").lower()
                if not any(term in label or term in href for term in CONTACT_PAGE_LINK_TEXT):
                    continue
                link.click(timeout=5000)
                page.wait_for_timeout(1500)
                return
            except Exception:
                continue

    def _supplemental_urls(self, page: Page) -> list[str]:
        """Returns a bounded set of same-site pages likely to contain legal/contact data."""
        base_host = urlparse(page.url).netloc.lower().removeprefix("www.")
        ranked: list[tuple[int, str]] = []
        links = page.locator("a[href]")
        for i in range(links.count()):
            try:
                href = links.nth(i).get_attribute("href") or ""
                label = (links.nth(i).inner_text(timeout=500) or "").lower()
                absolute = urljoin(page.url, href)
                parsed = urlparse(absolute)
                if parsed.scheme not in {"http", "https"} or parsed.netloc.lower().removeprefix("www.") != base_host:
                    continue
                haystack = f"{label} {parsed.path.lower()}"
                matches = [term for term in CONTACT_PAGE_LINK_TEXT if term in haystack]
                if matches:
                    ranked.append((0 if any(term in haystack for term in ("gst", "legal", "contact")) else 1, absolute))
            except Exception:
                continue
        return list(dict.fromkeys(url for _, url in sorted(ranked)))[:settings.ENRICHMENT_MAX_SUPPLEMENTAL_PAGES]

    def _extract_email(self, page: Page) -> Optional[str]:

        mailto_links = page.locator('a[href^="mailto:"]')

        if mailto_links.count() > 0:
            href = mailto_links.first.get_attribute("href") or ""
            email = href.replace("mailto:", "").split("?")[0].strip()

            if email:
                return email

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return None

        match = EMAIL_PATTERN.search(body_text)

        return match.group(0) if match else None

    def _extract_phones(self, page: Page) -> list[str]:
        """Returns distinct Indian mobile/landline numbers published on the page.

        tel: links are checked first (explicit intent, so trusted even if the
        visible text differs), then the body text is scanned for bare
        10-digit mobile numbers as a fallback for sites that show a number
        without wrapping it in a tel: link.
        """

        numbers: list[str] = []

        tel_links = page.locator('a[href^="tel:"]')
        for i in range(tel_links.count()):
            href = tel_links.nth(i).get_attribute("href") or ""
            number = href.replace("tel:", "").strip()
            if number and number not in numbers:
                numbers.append(number)

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            body_text = ""

        for match in PHONE_PATTERN.findall(body_text):
            if match not in numbers:
                numbers.append(match)

        return numbers

    @staticmethod
    def _extract_cin(page: Page) -> Optional[str]:
        try:
            content = page.content()
        except Exception:
            return None
        match = CIN_PATTERN.search(content)
        return match.group(0).upper() if match else None

    def _extract_address(self, page: Page) -> Optional[str]:
        """
        Looks for a line containing a 6-digit Indian PIN code that also
        looks address-like (has a comma, or a street/estate/etc word),
        then joins it with up to 2 preceding lines that look like part
        of the same address block.
        """

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return None

        lines = [line.strip() for line in body_text.splitlines() if line.strip()]

        for i, line in enumerate(lines):

            if not PINCODE_PATTERN.search(line):
                continue

            looks_address_like = "," in line or any(
                word in line.lower() for word in ADDRESS_HINT_WORDS
            )

            if not looks_address_like:
                continue

            preceding = lines[max(0, i - 2):i]
            address_lines = [
                l for l in preceding
                if "," in l or any(word in l.lower() for word in ADDRESS_HINT_WORDS)
            ]
            address_lines.append(line)

            return ", ".join(address_lines)

        return None

    # Contact-person/designation extraction (name shape, designation
    # canonicalization, candidate scoring/dedupe, and the per-container
    # LinkedIn-URL lookup that used to live here) now lives in
    # services/contact_extraction/ - see ContactExtractionPipeline above.
