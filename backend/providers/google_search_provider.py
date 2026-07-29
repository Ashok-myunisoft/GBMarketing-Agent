from typing import List
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from providers.base_provider import BaseProvider
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.browser_service import BrowserService


class GoogleSearchProvider(BaseProvider):
    """
    Google Organic Search Provider

    Responsibilities
    ----------------
    1. Build Google search query
    2. Open Google Search
    3. Parse search results
    4. Return Company objects
    """

    def __init__(self):
        self.browser = BrowserService()

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Google Search Provider ==========")

        query = self._build_query(request)

        print(f"Search Query : {query}")

        google_url = (
            f"https://www.google.com/search?q={quote_plus(query)}"
        )

        print(f"Google URL : {google_url}")

        try:

            html = self.browser.get_page_content(google_url)

            companies = self._parse_results(html)

            print(f"Google Results : {len(companies)}")

            return companies

        except Exception as ex:

            print(f"Google Search Failed : {ex}")

            return []

    def _build_query(
        self,
        request: SearchRequest
    ) -> str:

        query_parts = []

        if request.industry:
            query_parts.append(request.industry)

        if request.location:
            query_parts.append(request.location)

        if request.keywords:
            query_parts.extend(request.keywords)

        return " ".join(query_parts)

    def _parse_results(self, html: str) -> List[Company]:

        soup = BeautifulSoup(html, "html.parser")

        companies: List[Company] = []

        results = soup.select("div.g")

        for result in results:

            try:

                title = result.select_one("h3")

                link = result.select_one("a")

                if not title or not link:
                    continue

                company = Company(
                    company_name=title.get_text(strip=True),
                    website=link.get("href"),
                    phone=None,
                    email=None,
                    address=None,
                    city=None,
                    state=None
                )

                companies.append(company)

            except Exception:
                continue

        return companies

