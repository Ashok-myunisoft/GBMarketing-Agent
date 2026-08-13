from typing import List, Optional
from providers.base_provider import BaseProvider
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.tavily.tavily_search import TavilySearchService


class GoogleSearchProvider(BaseProvider):
    """
    Backwards-compatible search provider name.

    Responsibilities
    ----------------
    Older callers import this class by name, but production discovery is
    Tavily-only.  It intentionally never opens google.com or a browser.
    4. Return Company objects
    """

    def __init__(self):
        self._search = TavilySearchService()

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Tavily Search Provider ==========")

        query = self._build_query(request)

        print(f"Search Query : {query}")

        if not self._search.is_configured:
            return []
        try:
            results = self._search.search(query, max_results=request.max_results)
            companies = [
                Company(
                    company_name=result.title.strip() or result.url,
                    website=result.url,
                    phone=None, email=None, address=None, city=None, state=None,
                    industry=(result.content or result.title or None),
                )
                for result in results
            ]
            print(f"Tavily Results : {len(companies)}")
            return companies
        except Exception as ex:
            print(f"Tavily Search Failed : {ex}")
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


