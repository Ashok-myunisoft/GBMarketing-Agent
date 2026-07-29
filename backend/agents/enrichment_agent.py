import json
import logging
import re
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

from playwright.sync_api import BrowserContext, Page

from agents.base_agent import BaseClass
from config.geography import parse_address_components
from config.targeting import TARGET_DESIGNATIONS, extract_target_designation
from core.config import settings
from schemas.company import Company
from services.browser_service import BrowserService
from services.geocoding_service import GeoapifyGeocodingService
from services.filesure_service import FileSureService
from services.gst_turnover_service import GstTurnoverService
from services.llm_services import LLMService
from services.prompt_service import PromptService

logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_PATTERN = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}\b")
GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
CIN_PATTERN = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", re.IGNORECASE)
PINCODE_PATTERN = re.compile(r"\b\d{6}\b")
ADDRESS_HINT_WORDS = (
    "road", "street", "nagar", "estate", "industrial", "layout", "floor",
    "building", "complex", "colony", "phase", "block", "marg", "sector",
)
CONTACT_LINK_TEXT = "Contact"
CONTACT_PAGE_LINK_TEXT = (
    "contact", "about", "team", "management", "leadership", "legal",
    "privacy", "terms", "gst", "company-info",
)
MAX_SUPPLEMENTAL_PAGES = 5
MAX_PDF_BYTES = 5 * 1024 * 1024
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TOFLER_URL = "https://www.tofler.in/"
TOFLER_SEARCH_INPUT = 'input[placeholder="Search company, CIN OR DIN"]'
GOOGLE_SEARCH_URL = "https://www.google.com/search?q="
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
LINKEDIN_PEOPLE_SEARCH_URL = "https://www.linkedin.com/search/results/people/?keywords="
NAME_EXCLUDE_WORDS = {
    "pvt", "ltd", "limited", "private", "industries", "enterprises", "company",
    "corporation", "llp", "inc", "solutions", "technologies", "email", "phone",
    "contact", "address", "products", "services", "engineers", "engineering",
    "about", "us", "home", "careers", "our", "more", "read", "learn", "view",
    "get", "india", "private", "manufacturing", "coimbatore",
    "who", "are", "automation", "valves", "butterfly", "online", "retail",
    "channel", "customer", "centric", "approach", "major", "market", "bengaluru",
    "karnataka", "tamil", "nadu",
}


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
    ):
        self._browser = browser or BrowserService()
        self._geocoder = geocoder or GeoapifyGeocodingService()
        self._filesure = filesure or FileSureService()
        self._turnover = turnover or GstTurnoverService()
        self._prompt_service = PromptService()
        self._llm: Optional[LLMService] = None
        self._linkedin_context: Optional[BrowserContext] = None
        self._linkedin_authenticated = False
        self._linkedin_unavailable = False

    def execute(self, companies: List[Company]) -> List[Company]:

        print("\n========== Enrichment Agent Started ==========")

        owns_lifecycle = not self._browser.is_running

        if owns_lifecycle:
            self._browser.start()

        try:
            enriched = [self._enrich(company) for company in companies]
        finally:
            if self._linkedin_context is not None:
                self._linkedin_context.close()
                self._linkedin_context = None
            if owns_lifecycle:
                self._browser.stop()

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

    def _enrich(self, company: Company) -> Company:

        email = None
        address = None
        contact_person = None
        designation = None
        linkedin_url = None
        phones: list[str] = []
        gst = company.gst
        cin = company.cin
        turnover = company.turnover

        if company.website and company.website.startswith("http"):
            (
                email, address, contact_person, designation, linkedin_url,
                website_gst, website_cin, website_phones,
            ) = self._enrich_from_website(company)
            gst = gst or website_gst
            cin = cin or website_cin
            phones.extend(website_phones)

        if email is None or address is None:
            tofler_address, tofler_email = self._lookup_tofler(company.company_name)
            email = email or tofler_email
            address = address or tofler_address

        filesure_data = self._filesure.lookup(cin)
        if filesure_data:
            address = address or filesure_data.address
            gst = gst or filesure_data.gst
            contact_person = contact_person or filesure_data.contact_person
            designation = designation or filesure_data.designation

        # Google often exposes a GSTIN in a result snippet even when the
        # company site does not publish it. Only accept checksum-valid GSTINs.
        if not gst:
            gst = self._lookup_gst_on_google(company.company_name)

        # LinkedIn is a last resort: company websites and public registries
        # remain the preferred sources for named contacts.
        if not contact_person or not designation:
            linkedin_contact, linkedin_designation, linkedin_profile = self._lookup_linkedin_contact(company)
            contact_person = contact_person or linkedin_contact
            designation = designation or linkedin_designation
            linkedin_url = linkedin_url or linkedin_profile

        # The Jamku turnover lookup is intentionally downstream of GST
        # verification: it never receives a guessed or malformed GSTIN.
        if gst and not turnover:
            slab = self._turnover.lookup(gst)
            if slab:
                turnover = slab.label

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

        city, state, region = parse_address_components(address or company.address)
        geocoded = self._geocoder.geocode(address or company.address)
        if geocoded:
            city = geocoded.city or city
            state = geocoded.state or state
            region = geocoded.region

        if all(
            v is None
            for v in (email, address, contact_person, designation, linkedin_url, gst, cin, turnover)
        ) and phone_alt is None:
            return company

        return company.model_copy(
            update={
                "email": email or company.email,
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
            }
        )

    def _lookup_gst_on_google(self, company_name: str) -> Optional[str]:
        """Uses the exact company-GST Google query and the GST prompt template."""
        try:
            page = self._browser.new_page()
            try:
                search_query = f'"{company_name}" gst number'
                self._browser.goto(page, f"{GOOGLE_SEARCH_URL}{quote_plus(search_query)}")
                page.wait_for_timeout(1500)
                result_text = page.locator("body").inner_text(timeout=5000)
                return self._extract_gst_with_prompt(company_name, search_query, result_text)
            finally:
                page.close()
        except Exception as ex:
            logger.warning("Google GST lookup failed for '%s': %s", company_name, ex)
            return None

    def _extract_gst_with_prompt(
        self, company_name: str, search_query: str, result_text: str
    ) -> Optional[str]:
        """Uses the GST prompt template to select a candidate from Google text."""
        if not result_text.strip():
            return None
        try:
            template = self._prompt_service.load("gst_enrichment")
            system_prompt = (
                template
                .replace("{{company_name}}", company_name)
                .replace("{{search_query}}", search_query)
            )
            user_prompt = f"Google result text:\n{result_text[:12000]}"
            if self._llm is None:
                self._llm = LLMService()
            response = self._llm.invoke(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=100,
            )
            candidate = json.loads(response).get("gst")
            return self._valid_gstin_from_text(str(candidate or ""))
        except Exception as ex:
            logger.info("GST prompt extraction unavailable for '%s': %s", company_name, ex)
            return None

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
                designation = self._target_designation_in_text(text)
                profile = card.locator('a[href*="/in/"]').first
                if not designation or profile.count() == 0:
                    continue
                name = (profile.inner_text(timeout=1000) or "").strip().splitlines()[0]
                if self._looks_like_person_name(name):
                    href = profile.get_attribute("href") or ""
                    return self._normalise_person_name(name), designation, href.split("?")[0]
            except Exception:
                continue
        return None, None, None

    @staticmethod
    def _target_designation_in_text(text: str) -> Optional[str]:
        normalized = re.sub(r"\s+", " ", text or "").lower()
        candidates = [title for group in TARGET_DESIGNATIONS for title in group["titles"]]
        for candidate in sorted(candidates, key=len, reverse=True):
            if re.search(r"\b" + re.escape(candidate.lower()) + r"\b", normalized):
                return candidate
        return None

    @staticmethod
    def _digits(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        digits = re.sub(r"\D+", "", value)
        return digits[-10:] if len(digits) >= 10 else digits or None

    def _enrich_from_website(
        self, company: Company
    ) -> "tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], Optional[str], list[str]]":
        """Returns email, address, contact, designation, LinkedIn URL, public GSTIN, CIN, and phone numbers found."""

        try:
            page = self._browser.new_page()

            try:
                self._browser.goto(page, company.website)
                page.wait_for_timeout(1500)

                email = self._extract_email(page)
                address = self._extract_address(page)
                contact_person, designation, linkedin_url = self._extract_contact(page)
                gst = self._extract_gst(page)
                cin = self._extract_cin(page)
                pdf_gst = self._extract_gst_from_public_pdfs(page)
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
                    gst = gst or self._extract_gst(page)
                    cin = cin or self._extract_cin(page)
                    pdf_gst = pdf_gst or self._extract_gst_from_public_pdfs(page)
                    if contact_person is None:
                        contact_person, designation, linkedin_url = self._extract_contact(page)
                    if len(phones) < 2:
                        phones = list(dict.fromkeys(phones + self._extract_phones(page)))

                    if email and address and contact_person and gst and len(phones) >= 2:
                        break

                gst = gst or pdf_gst

                return email, address, contact_person, designation, linkedin_url, gst, cin, phones

            finally:
                page.close()

        except Exception as ex:
            logger.warning(
                "Website enrichment failed for '%s' (%s): %s",
                company.company_name,
                company.website,
                ex,
            )
            return None, None, None, None, None, None, None, []

    def _lookup_tofler(self, company_name: str) -> "tuple[Optional[str], Optional[str]]":
        """Returns (address, email) from Tofler's free registered-details lookup, or (None, None)."""

        try:
            page = self._browser.new_page()

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
        return list(dict.fromkeys(url for _, url in sorted(ranked)))[:MAX_SUPPLEMENTAL_PAGES]

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

    def _extract_gst(self, page: Page) -> Optional[str]:
        """Finds a public Indian GSTIN in page text or embedded HTML.

        The GSTIN format is distinct enough to find it even when it appears in
        a footer, legal modal, JSON-LD, or an otherwise hidden HTML section;
        no guessed GST values are ever produced. Password-protected pages,
        images, and unlinked PDFs remain intentionally out of scope.
        """
        try:
            content = page.content()
        except Exception:
            return None
        return self._valid_gstin_from_text(content)

    @staticmethod
    def _extract_cin(page: Page) -> Optional[str]:
        try:
            content = page.content()
        except Exception:
            return None
        match = CIN_PATTERN.search(content)
        return match.group(0).upper() if match else None

    def _extract_gst_from_public_pdfs(self, page: Page) -> Optional[str]:
        """Scans a few public same-site PDF documents for a verified GSTIN."""
        try:
            from pypdf import PdfReader
        except ImportError:
            return None
        base_host = urlparse(page.url).netloc.lower().removeprefix("www.")
        links = page.locator('a[href*=".pdf" i]')
        for i in range(min(links.count(), 3)):
            try:
                url = urljoin(page.url, links.nth(i).get_attribute("href") or "")
                if urlparse(url).netloc.lower().removeprefix("www.") != base_host:
                    continue
                request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urlopen(request, timeout=15) as response:
                    payload = response.read(MAX_PDF_BYTES + 1)
                if len(payload) > MAX_PDF_BYTES:
                    continue
                text = "\n".join((page.extract_text() or "") for page in PdfReader(BytesIO(payload)).pages[:10])
                gst = self._valid_gstin_from_text(text)
                if gst:
                    return gst
            except Exception:
                continue
        return None

    @staticmethod
    def _valid_gstin_from_text(text: str) -> Optional[str]:
        for raw in GSTIN_PATTERN.findall(text or ""):
            gst = raw.upper()
            total, factor = 0, 1
            for char in gst[:-1]:
                value = GSTIN_CHARSET.index(char) * factor
                total += value // 36 + value % 36
                factor = 2 if factor == 1 else 1
            expected = GSTIN_CHARSET[(36 - total % 36) % 36]
            if gst[-1] == expected:
                return gst
        return None

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

    def _extract_contact(self, page: Page) -> "tuple[Optional[str], Optional[str], Optional[str]]":
        """Returns a target-role contact published on the current page, if any.

        Website text is deliberately treated as untrusted: a name is accepted only
        when it appears close to a title that matches our designation taxonomy.
        This avoids turning product names, navigation labels, or generic support
        emails into fabricated people records.
        """

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            return None, None, None

        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            for name, title in self._name_title_candidates(line):
                designation = extract_target_designation(title)
                if designation and self._looks_like_person_name(name):
                    return self._normalise_person_name(name), designation, self._extract_linkedin_url(page)

            designation = extract_target_designation(line)
            if not designation:
                continue
            neighbors = lines[max(0, index - 1):index] + lines[index + 1:index + 2]
            person = next((candidate for candidate in neighbors if self._looks_like_person_name(candidate)), None)
            if person:
                return self._normalise_person_name(person), designation, self._extract_linkedin_url(page)

        return None, None, self._extract_linkedin_url(page)

    @staticmethod
    def _name_title_candidates(line: str) -> "list[tuple[str, str]]":
        """Returns (name, title) pairs a single line could plausibly encode.

        Covers "Name (Title)" as before, plus the common team-listing
        variants "Name - Title", "Name, Title", and "Name | Title" - in
        both orders, since some sites list the title before the name.
        Both halves of every candidate still have to clear the same
        designation-taxonomy and name-shape checks as the parenthetical
        case, so this widens *where* a match can be found, not what
        counts as a match.
        """
        candidates: list[tuple[str, str]] = []

        parenthetical = re.fullmatch(r"\s*([A-Za-z][A-Za-z .'-]{2,80}?)\s*\(([^()]{2,80})\)\s*", line)
        if parenthetical:
            candidates.append(parenthetical.groups())

        parts = re.split(r"\s*[-,|]\s*", line, maxsplit=1)
        if len(parts) == 2 and all(2 <= len(part) <= 80 for part in parts):
            candidates.append((parts[0], parts[1]))
            candidates.append((parts[1], parts[0]))

        return candidates

    @staticmethod
    def _looks_like_person_name(value: str) -> bool:
        words = [word.strip(".,()") for word in value.split()]
        if not 2 <= len(words) <= 5:
            return False
        if any(word.lower() in NAME_EXCLUDE_WORDS for word in words):
            return False
        return all(word and word.replace("-", "").isalpha() for word in words)

    @staticmethod
    def _normalise_person_name(value: str) -> str:
        return " ".join(word.capitalize() for word in value.strip().split())

    @staticmethod
    def _extract_linkedin_url(page: Page) -> Optional[str]:
        links = page.locator('a[href*="linkedin.com"]')
        for i in range(links.count()):
            href = links.nth(i).get_attribute("href")
            if href and "/in/" in href:
                return href
        return None
