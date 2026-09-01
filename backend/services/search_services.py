from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from schemas.company import Company
from schemas.search_request import SearchRequest

from providers.google_search_provider import GoogleSearchProvider
from providers.google_maps_provider import GoogleMapsProvider
from providers.business_directory_provider import BusinessDirectoryProvider
from config.geography import canonical_city, location_query_variants
from config.targeting import search_industry_queries
from core.config import settings
from services.locality_service import GeoapifyLocalityService


class SearchService:
    """
    Coordinates all search providers and returns
    a unified list of discovered companies.
    """

    def __init__(self, locality_service: Optional[GeoapifyLocalityService] = None):

        self.providers = [
            GoogleSearchProvider(),
            GoogleMapsProvider(),
            BusinessDirectoryProvider()
        ]

        # Off by default (settings.LOCALITY_DISCOVERY_ENABLED) - see
        # _location_variants_for below.
        self._locality_service = locality_service or GeoapifyLocalityService()

        # Populated by search(); read by SearchAgent/WorkflowOrchestrator to
        # report the raw-vs-deduped funnel and why each cross-provider
        # duplicate was dropped, without changing this method's return type.
        self.last_run_stats: dict = {"raw_fetched": 0, "after_dedup": 0, "removed": []}

    def search(self, request: SearchRequest) -> List[Company]:

        print("\n========== Search Service Started ==========")

        industries = search_industry_queries(request.industry)
        locations = self._location_variants_for(request.location)
        primary_location, *fallback_locations = locations
        per_query_limit = min(request.max_results, settings.SEARCH_RESULTS_PER_QUERY)
        all_companies: List[Company] = []

        def search_location(location: Optional[str]) -> None:
            query_requests = [
                request.model_copy(
                    update={"industry": industry, "location": location, "max_results": per_query_limit}
                )
                for industry in industries
            ]
            all_companies.extend(self._search_requests(query_requests))

        # The city is the broadest, highest-value query.  Only search a
        # locality when it is actually needed to reach the desired lead count.
        search_location(primary_location)
        companies, removed = self._remove_duplicates(all_companies)
        for locality in fallback_locations:
            if len(companies) >= request.max_results:
                break
            search_location(locality)
            companies, removed = self._remove_duplicates(all_companies)

        self.last_run_stats = {
            "raw_fetched": len(all_companies),
            "after_dedup": len(companies),
            "removed": removed,
        }

        print(f"\nTotal Companies : {len(companies)}")

        print("========== Search Service Completed ==========\n")

        return companies[:request.max_results]

    def _search_requests(self, query_requests: List[SearchRequest]) -> List[Company]:
        """Run a small city/locality batch across providers in parallel."""
        all_companies: List[Company] = []

        # One worker thread per provider, each running its own small batch in
        # sequence.  Browser-backed providers remain thread-affine.
        with ThreadPoolExecutor(
            max_workers=max(1, len(self.providers)), thread_name_prefix="search-provider"
        ) as executor:
            futures = [
                executor.submit(self._run_provider_queries, provider, query_requests)
                for provider in self.providers
            ]
            for future in futures:
                all_companies.extend(future.result())

        return all_companies

    def _location_variants_for(self, location: Optional[str]) -> "list[Optional[str]]":
        """
        Starts from the static locality seed and, only when discovery is on,
        requests no more than the configured number of additional localities
        from Geoapify.  Search() uses these as fallback queries after the
        broad city query, rather than running every variant unconditionally.
        """

        variants = location_query_variants(location)

        if not settings.LOCALITY_DISCOVERY_ENABLED or not self._locality_service.is_configured:
            return variants

        city = canonical_city(location) or (location or "").strip()

        if not city:
            return variants

        discovered = self._locality_service.localities_for(
            city, max_localities=settings.LOCALITY_DISCOVERY_MAX_VARIANTS
        )

        if not discovered:
            return variants

        existing = {(variant or "").strip().lower() for variant in variants}
        extra = []

        for name in discovered:
            candidate = f"{name}, {city}"
            if candidate.strip().lower() not in existing:
                existing.add(candidate.strip().lower())
                extra.append(candidate)

        return variants + extra

    def _run_provider_queries(
        self,
        provider,
        query_requests: List[SearchRequest],
    ) -> List[Company]:
        """
        Runs every industry/location query against one provider, on
        whichever worker thread this was submitted to.

        Providers backed by a browser (GoogleMapsProvider,
        BusinessDirectoryProvider) expose it as `_browser`; starting it
        once here - before any of this provider's queries run - and
        stopping it once after the last means every individual
        `provider.search()` call below sees the browser already running
        and reuses it, instead of each call launching and tearing down
        its own browser process. Providers with no `_browser` (e.g. the
        Tavily-backed GoogleSearchProvider) are unaffected.
        """

        browser = getattr(provider, "_browser", None)
        owns_lifecycle = browser is not None and not browser.is_running

        if owns_lifecycle:
            browser.start()

        try:
            companies: List[Company] = []

            for query_request in query_requests:
                try:
                    print(
                        f"\nExecuting Provider : {provider.__class__.__name__} "
                        f"({query_request.industry} | {query_request.location})"
                    )
                    # `company.industry` is left exactly as the provider
                    # observed it (or None) - never overwritten with the
                    # taxonomy term used to build this query. Stamping the
                    # search term here would make ValidationAgent's industry
                    # check tautological: it would always match itself,
                    # regardless of what the company's real business is.
                    found = provider.search(query_request)
                    print(f"Found {len(found)} companies")
                    companies.extend(found)
                except Exception as ex:
                    print(f"{provider.__class__.__name__} failed : {str(ex)}")

            return companies

        finally:
            if owns_lifecycle:
                browser.stop()

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
