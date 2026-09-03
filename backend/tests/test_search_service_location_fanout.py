import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# SearchService pulls in providers that import playwright at module scope.
# Stub it out so this test can exercise SearchService.search()'s fan-out
# loop in isolation, the same way the GST/turnover tests already do.
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

from schemas.company import Company
from schemas.search_request import SearchRequest
from services.search_services import SearchService


class SearchServiceLocationFanoutTests(unittest.TestCase):
    def _service_with_stub_providers(self):
        service = SearchService()
        stub_provider = MagicMock()
        stub_provider.search.return_value = []
        service.providers = [stub_provider]
        return service, stub_provider

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    def test_unseeded_location_runs_exactly_one_query(self, _industries):
        service, stub_provider = self._service_with_stub_providers()
        service.search(SearchRequest(industry="Valve", location="Dubai"))

        self.assertEqual(stub_provider.search.call_count, len(service._location_variants_for("Dubai")))
        queried_locations = [call.args[0].location for call in stub_provider.search.call_args_list]
        self.assertEqual(queried_locations[0], "Dubai")

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    def test_seeded_city_fans_out_once_per_known_locality(self, _industries):
        service, stub_provider = self._service_with_stub_providers()
        service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        expected_calls = len(service._location_variants_for("Coimbatore"))
        self.assertEqual(stub_provider.search.call_count, expected_calls)

        queried_locations = {call.args[0].location for call in stub_provider.search.call_args_list}
        self.assertIn("Coimbatore", queried_locations)
        self.assertIn("Peelamedu, Coimbatore", queried_locations)

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    def test_results_across_location_variants_are_returned_without_search_deduplication(self, _industries):
        service, stub_provider = self._service_with_stub_providers()
        # Search forwards every provider/query result. Validation owns all
        # duplicate removal later in the workflow.
        stub_provider.search.return_value = [
            Company(company_name="Ambica Electro Control", website="https://ambica.example")
        ]

        results = service.search(SearchRequest(industry="Valve", location="Coimbatore"))

        self.assertEqual(len(results), stub_provider.search.call_count)
        self.assertEqual(service.last_run_stats["removed"], [])
        self.assertGreater(stub_provider.search.call_count, 1)

    @patch("services.search_services.search_industry_queries", return_value=["Valve"])
    def test_a_failing_provider_on_one_location_variant_does_not_stop_the_others(self, _industries):
        service, stub_provider = self._service_with_stub_providers()
        stub_provider.search.side_effect = [RuntimeError("boom")] + [[] for _ in range(20)]

        # Must not raise - a single provider/query failure is swallowed so
        # the remaining location variants still run.
        service.search(SearchRequest(industry="Valve", location="Coimbatore"))
        self.assertGreater(stub_provider.search.call_count, 1)


if __name__ == "__main__":
    unittest.main()
