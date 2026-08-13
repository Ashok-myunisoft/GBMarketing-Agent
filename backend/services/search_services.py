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

        # Populated by search(); read by SearchAgent/WorkflowOrchestrator to
        # report the raw-vs-deduped funnel and why each cross-provider
        # duplicate was dropped, without changing this method's return type.
        self.last_run_stats: dict = {"raw_fetched": 0, "after_dedup": 0, "removed": []}

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

        companies, removed = self._remove_duplicates(all_companies)

        self.last_run_stats = {
            "raw_fetched": len(all_companies),
            "after_dedup": len(companies),
            "removed": removed,
        }

        print(f"\nTotal Companies : {len(companies)}")

        print("========== Search Service Completed ==========\n")

        return companies[:request.max_results]

    def _remove_duplicates(
        self,
        companies: List[Company]
    ) -> "tuple[List[Company], List[dict]]":
        """
        Remove duplicate companies based on company name + website, and
        report the specific ones dropped (e.g. the same business found by
        both GoogleMapsProvider and BusinessDirectoryProvider).
        """

        unique = {}
        removed: List[dict] = []

        for company in companies:

            key = (
                (company.company_name or "").strip().lower(),
                (company.website or "").strip().lower()
            )

            if key not in unique:
                unique[key] = company
            else:
                removed.append({
                    "company_name": company.company_name or "(unnamed)",
                    "reason": "duplicate result for the same company found by another provider/query",
                })

        return list(unique.values()), removed
