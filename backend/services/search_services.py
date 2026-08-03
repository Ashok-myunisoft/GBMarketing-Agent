from math import ceil
from typing import List

from schemas.company import Company
from schemas.search_request import SearchRequest

from providers.google_search_provider import GoogleSearchProvider
from providers.google_maps_provider import GoogleMapsProvider
from providers.business_directory_provider import BusinessDirectoryProvider
from config.targeting import search_industry_queries


class SearchService:
    """
    Coordinates all search providers and returns
    a unified list of discovered companies.
    """

    def __init__(self):

        self.providers = [
            GoogleSearchProvider(),
            GoogleMapsProvider(),
            BusinessDirectoryProvider()
        ]

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Search Service Started ==========")

        all_companies: List[Company] = []

        industries = search_industry_queries(request.industry)
        per_query_limit = max(1, ceil(request.max_results / len(industries)))
        for industry in industries:
            query_request = request.model_copy(update={"industry": industry, "max_results": per_query_limit})
            for provider in self.providers:
                try:
                    print(f"\nExecuting Provider : {provider.__class__.__name__} ({industry})")
                    # `company.industry` is left exactly as the provider
                    # observed it (or None) - never overwritten with the
                    # taxonomy term used to build this query. Stamping the
                    # search term here would make ValidationAgent's industry
                    # check tautological: it would always match itself,
                    # regardless of what the company's real business is.
                    companies = provider.search(query_request)
                    print(f"Found {len(companies)} companies")
                    all_companies.extend(companies)
                except Exception as ex:
                    print(f"{provider.__class__.__name__} failed : {str(ex)}")

        companies = self._remove_duplicates(all_companies)

        print(f"\nTotal Companies : {len(companies)}")

        print("========== Search Service Completed ==========\n")

        return companies[:request.max_results]

    def _remove_duplicates(
        self,
        companies: List[Company]
    ) -> List[Company]:
        """
        Remove duplicate companies based on
        company name + website.
        """

        unique = {}

        for company in companies:

            key = (
                (company.company_name or "").strip().lower(),
                (company.website or "").strip().lower()
            )

            if key not in unique:
                unique[key] = company

        return list(unique.values())
