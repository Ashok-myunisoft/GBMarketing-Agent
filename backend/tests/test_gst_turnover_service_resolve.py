import sys
import types
import unittest
from unittest.mock import MagicMock, patch

fake_playwright = types.ModuleType("playwright")
fake_playwright_sync = types.ModuleType("playwright.sync_api")
fake_playwright_sync.sync_playwright = lambda *args, **kwargs: None
fake_playwright_sync.Playwright = object
fake_playwright_sync.Browser = object
fake_playwright_sync.BrowserContext = object
fake_playwright_sync.Page = object
fake_playwright_sync.Error = Exception
fake_playwright_sync.TimeoutError = Exception
sys.modules["playwright"] = fake_playwright
sys.modules["playwright.sync_api"] = fake_playwright_sync

from services.gst_turnover_enrichment.firecrawl_client import FirecrawlPage, FirecrawlSearchResult
from services.gst_turnover_enrichment.service import GstTurnoverEnrichmentService


class ResolveTests(unittest.TestCase):
    def _service(self, results):
        firecrawl = MagicMock()
        firecrawl.search.return_value = results
        return GstTurnoverEnrichmentService(browser=MagicMock(), firecrawl=firecrawl), firecrawl

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_searches_company_name_without_scraping_website(self, _validate):
        service, firecrawl = self._service([
            FirecrawlSearchResult(
                url="https://directory.example/ambica",
                title="Ambica Electro Control GSTIN",
                markdown="Ambica Electro Control GSTIN: 27AAPFU0939F1ZV\nAnnual turnover: Rs. 50 Crore",
            )
        ])
        result = service.resolve("Ambica Electro Control", "https://dead.example")

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        self.assertEqual(result.turnover.value, "50 Crore")
        self.assertTrue(all("Ambica Electro Control" in call.args[0] for call in firecrawl.search.call_args_list))
        firecrawl.scrape.assert_not_called()

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_rejects_similarly_named_company(self, _validate):
        service, _firecrawl = self._service([
            FirecrawlSearchResult(
                url="https://directory.example/other",
                title="Ambica Electricals Ahmedabad",
                markdown="Ambica Electricals GSTIN: 27AAPFU0939F1ZV Annual turnover 50 Crore",
            )
        ])
        result = service.resolve("Ambica Electro Control", None, city="Pune", state="Maharashtra")

        self.assertIsNone(result.gst.value or None)
        self.assertIsNone(result.turnover.value or None)

    def test_search_failure_is_non_fatal(self):
        service, firecrawl = self._service([])
        result = service.resolve("Ambica Electro Control", None)

        self.assertEqual(result.gst.status, "not_found")
        self.assertEqual(result.turnover.status, "not_found")
        self.assertGreaterEqual(firecrawl.search.call_count, 2)

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    @patch("services.gst_turnover_enrichment.service.settings.ENRICHMENT_LOOKUP_TURNOVER", False)
    def test_scrapes_result_url_when_description_alone_is_insufficient(self, _validate):
        # Firecrawl Cloud Search returns only title/description, never page
        # content - a FirecrawlSearchResult with no markdown must fall back
        # to scrape() (existing self-hosted mechanism) for more evidence.
        # Turnover lookup is disabled here so the scrape call count is exact.
        service, firecrawl = self._service([
            FirecrawlSearchResult(
                url="https://directory.example/ambica",
                title="Ambica Electro Control - Company Profile",
                description="Business directory listing for Ambica Electro Control",
            )
        ])
        firecrawl.scrape.return_value = FirecrawlPage(
            url="https://directory.example/ambica",
            markdown="Ambica Electro Control GSTIN: 27AAPFU0939F1ZV",
            success=True,
        )

        result = service.resolve("Ambica Electro Control", None)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        firecrawl.scrape.assert_called_once_with("https://directory.example/ambica")

    def test_duplicate_search_results_are_deduplicated_by_normalized_url(self):
        # Same page reachable via a trailing slash and an uppercase host -
        # must collapse to a single ranked result, not two.
        results = [
            FirecrawlSearchResult(url="https://Directory.example/ambica/", title="a"),
            FirecrawlSearchResult(url="https://directory.example/ambica", title="b"),
            FirecrawlSearchResult(url="https://directory.example/other", title="c"),
        ]
        ranked = GstTurnoverEnrichmentService._rank_results(
            "Ambica Electro Control", "gst", results, None, None, None
        )
        self.assertEqual(len(ranked), 2)


if __name__ == "__main__":
    unittest.main()
