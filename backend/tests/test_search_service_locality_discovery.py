import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# SearchService pulls in providers that import playwright at module scope -
# stub it out the same way test_search_service_location_fanout.py does, so
# this test can exercise SearchService.search() in isolation.
fake_playwright = types.ModuleType("playwright")
fake_playwright_sync = types.ModuleType("playwright.sync_api")
fake_playwright_sync.sync_playwright = lambda *args, **kwargs: None
fake_playwright_sync.Playwright = object
fake_playwright_sync.Browser = object
fake_playwright_sync.BrowserContext = object
fake_playwright_sync.Page = object
fake_playwright_sync.Locator = object
fake_playwright_sync.Error = Exception
fake_playwright_sync.TimeoutError = Exception
sys.modules["playwright"] = fake_playwright
sys.modules["playwright.sync_api"] = fake_playwright_sync

from services.search_services import SearchService


class SearchServiceLocalityDiscoveryTests(unittest.TestCase):
    def _service_with_stub_provider_and_locality_service(self):
        locality_service = MagicMock()
        locality_service.is_configured = True
        service = SearchService(locality_service=locality_service)
        stub_provider = MagicMock()
        stub_provider.search.return_value = []
        service.providers = [stub_provider]
        return service, stub_provider, locality_service

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    @patch("services.search_services.settings")
    def test_disabled_by_default_never_touches_the_locality_service(self, mock_settings, _industries):
        mock_settings.LOCALITY_DISCOVERY_ENABLED = False
        mock_settings.SEARCH_RESULTS_PER_QUERY = 20
        service, stub_provider, locality_service = self._service_with_stub_provider_and_locality_service()

        from schemas.search_request import SearchRequest
        service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        locality_service.localities_for.assert_not_called()
        from config.geography import CITY_LOCALITIES
        self.assertEqual(stub_provider.search.call_count, 1 + len(CITY_LOCALITIES["Coimbatore"]))

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    @patch("services.search_services.settings")
    def test_enabled_adds_discovered_localities_beyond_the_static_seed(self, mock_settings, _industries):
        mock_settings.LOCALITY_DISCOVERY_ENABLED = True
        mock_settings.LOCALITY_DISCOVERY_MAX_VARIANTS = 5
        mock_settings.SEARCH_RESULTS_PER_QUERY = 20
        service, stub_provider, locality_service = self._service_with_stub_provider_and_locality_service()
        locality_service.localities_for.return_value = ["Peelamedu", "Podanur", "Kuniyamuthur"]

        from schemas.search_request import SearchRequest
        from config.geography import CITY_LOCALITIES
        service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        locality_service.localities_for.assert_called_once_with("Coimbatore", max_localities=5)
        # Static seed (city + its 5 known localities) plus the genuinely new
        # discovered locality ("Podanur", "Kuniyamuthur" - not already seeded).
        # "Peelamedu" is already in the static seed, so it must not be
        # queried twice.
        expected = 1 + len(CITY_LOCALITIES["Coimbatore"]) + 2
        self.assertEqual(stub_provider.search.call_count, expected)

        queried_locations = {call.args[0].location for call in stub_provider.search.call_args_list}
        self.assertIn("Podanur, Coimbatore", queried_locations)
        self.assertIn("Kuniyamuthur, Coimbatore", queried_locations)

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    @patch("services.search_services.settings")
    def test_enabled_but_unconfigured_locality_service_falls_back_to_static_seed(self, mock_settings, _industries):
        mock_settings.LOCALITY_DISCOVERY_ENABLED = True
        mock_settings.SEARCH_RESULTS_PER_QUERY = 20
        service, stub_provider, locality_service = self._service_with_stub_provider_and_locality_service()
        locality_service.is_configured = False

        from schemas.search_request import SearchRequest
        from config.geography import CITY_LOCALITIES
        service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        locality_service.localities_for.assert_not_called()
        self.assertEqual(stub_provider.search.call_count, 1 + len(CITY_LOCALITIES["Coimbatore"]))

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    @patch("services.search_services.settings")
    def test_enabled_for_an_unseeded_city_still_discovers_localities(self, mock_settings, _industries):
        mock_settings.LOCALITY_DISCOVERY_ENABLED = True
        mock_settings.LOCALITY_DISCOVERY_MAX_VARIANTS = 5
        mock_settings.SEARCH_RESULTS_PER_QUERY = 20
        service, stub_provider, locality_service = self._service_with_stub_provider_and_locality_service()
        locality_service.localities_for.return_value = ["Andheri", "Bandra"]

        from schemas.search_request import SearchRequest
        service.search(SearchRequest(industry="Valve", location="Mumbai"))

        locality_service.localities_for.assert_called_once_with("Mumbai", max_localities=5)
        self.assertEqual(stub_provider.search.call_count, 3)  # Mumbai + Andheri + Bandra

        queried_locations = {call.args[0].location for call in stub_provider.search.call_args_list}
        self.assertEqual(queried_locations, {"Mumbai", "Andheri, Mumbai", "Bandra, Mumbai"})

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    @patch("services.search_services.settings")
    def test_city_results_stop_locality_fallback(self, mock_settings, _industries):
        mock_settings.LOCALITY_DISCOVERY_ENABLED = False
        mock_settings.SEARCH_RESULTS_PER_QUERY = 20
        service, stub_provider, _locality_service = self._service_with_stub_provider_and_locality_service()
        from schemas.company import Company
        from schemas.search_request import SearchRequest
        stub_provider.search.return_value = [
            Company(company_name=f"Company {number}", website=f"https://{number}.example")
            for number in range(50)
        ]

        results = service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        self.assertEqual(len(results), 50)
        self.assertEqual(stub_provider.search.call_count, 1)
        self.assertEqual(stub_provider.search.call_args.args[0].max_results, 20)


if __name__ == "__main__":
    unittest.main()
