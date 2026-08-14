from concurrent.futures import ThreadPoolExecutor
from math import ceil
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

        all_companies: List[Company] = []

        industries = search_industry_queries(request.industry)
        # Fans a single "<industry> <city>" query out into one query per
        # known locality of that city too (e.g. "<industry> Peelamedu,
        # Coimbatore") - each provider only ever returns its own top results
        # for one exact query, so more distinctly-worded queries is what
        # surfaces a broader slice of real businesses instead of the same
        # top-ranked handful every time. Cities with no seeded localities
        # (config/geography.CITY_LOCALITIES) fall back to the single
        # original location - unchanged behaviour.
        locations = self._location_variants_for(request.location)
        per_query_limit = max(1, ceil(request.max_results / (len(industries) * len(locations))))

        query_requests = [
            request.model_copy(
                update={"industry": industry, "location": location, "max_results": per_query_limit}
            )
            for industry in industries
            for location in locations
        ]

        # One worker thread per provider, each running that provider's full
        # set of industry/location queries in sequence - not one worker per
        # query. Playwright's sync API is thread-affine (see EnrichmentAgent):
        # a single browser must only ever be driven from the thread that
        # started it, so GoogleMapsProvider/BusinessDirectoryProvider's
        # shared browser can't be hit by multiple threads at once. Keeping
        # exactly one thread per provider satisfies that while still letting
        # the (up to) 3 providers - previously fully serial - run
        # concurrently, and lets _run_provider_queries start each provider's
        # browser once and reuse it for every query instead of relaunching
        # per call.
        with ThreadPoolExecutor(
            max_workers=max(1, len(self.providers)), thread_name_prefix="search-provider"
        ) as executor:
            futures = [
                executor.submit(self._run_provider_queries, provider, query_requests)
                for provider in self.providers
            ]
            for future in futures:
                all_companies.extend(future.result())

        companies, removed = self._remove_duplicates(all_companies)

        self.last_run_stats = {
            "raw_fetched": len(all_companies),
            "after_dedup": len(companies),
            "removed": removed,
        }

        print(f"\nTotal Companies : {len(companies)}")

        print("========== Search Service Completed ==========\n")

        return companies[:request.max_results]

    def _location_variants_for(self, location: Optional[str]) -> "list[Optional[str]]":
        """
        Starts from config/geography.location_query_variants() (the static
        CITY_LOCALITIES seed - a handful of localities for a handful of
        cities, unchanged behaviour) and, only when
        settings.LOCALITY_DISCOVERY_ENABLED is on and Geoapify is
        configured, adds every extra suburb/neighbourhood Geoapify itself
        knows for this city - seeded or not - so coverage isn't capped at
        whatever's been hand-added to the static list. Purely additive:
        the static variants are always included, and any failure to reach
        Geoapify just means no extra variants this run, never fewer.
        """

        variants = location_query_variants(location)

        if not settings.LOCALITY_DISCOVERY_ENABLED or not self._locality_service.is_configured:
            return variants

        city = canonical_city(location) or (location or "").strip()

        if not city:
            return variants

        discovered = self._locality_service.localities_for(city)

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
