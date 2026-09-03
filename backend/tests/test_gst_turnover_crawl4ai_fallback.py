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

from services.gst_turnover_enrichment.crawl4ai_client import Crawl4AIPage
from services.gst_turnover_enrichment.firecrawl_client import FirecrawlPage, FirecrawlSearchResult
from services.gst_turnover_enrichment.service import GstTurnoverEnrichmentService

COMPANY = "Ambica Electro Control"
WEBSITE = "https://ambicaelectro.example"


def _service(search_results, crawl4ai_page=None):
    firecrawl = MagicMock()
    firecrawl.search.return_value = search_results
    firecrawl.scrape.return_value = FirecrawlPage(url="", success=False)
    crawl4ai = MagicMock()
    crawl4ai.crawl.return_value = crawl4ai_page or Crawl4AIPage(url=WEBSITE, error="unused in this test")
    service = GstTurnoverEnrichmentService(browser=MagicMock(), firecrawl=firecrawl, crawl4ai=crawl4ai)
    return service, firecrawl, crawl4ai


@patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
class Crawl4AIFallbackTests(unittest.TestCase):

    def test_firecrawl_success_skips_crawl4ai(self, _validate):
        """Test 1: Firecrawl finds both fields -> Crawl4AI is never called."""
        service, _firecrawl, crawl4ai = _service([
            FirecrawlSearchResult(
                url="https://directory.example/ambica",
                title="Ambica Electro Control GSTIN",
                markdown="Ambica Electro Control GSTIN: 27AAPFU0939F1ZV\nAnnual turnover: Rs. 50 Crore",
            )
        ])
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        self.assertEqual(result.turnover.value, "50 Crore")
        crawl4ai.crawl.assert_not_called()

    def test_firecrawl_failure_triggers_crawl4ai_for_both_fields(self, _validate):
        """Test 2: Firecrawl search yields nothing at all -> Crawl4AI covers both fields."""
        service, _firecrawl, crawl4ai = _service(
            [],
            crawl4ai_page=Crawl4AIPage(
                url=WEBSITE, success=True,
                content=f"{COMPANY} GSTIN: 27AAPFU0939F1ZV\nAnnual turnover: Rs. 50 Crore",
            ),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        self.assertEqual(result.turnover.value, "50 Crore")
        self.assertEqual(crawl4ai.crawl.call_count, 2)
        crawl4ai.crawl.assert_called_with(WEBSITE)

    def test_gst_found_turnover_missing_calls_crawl4ai_only_for_turnover(self, _validate):
        """Test 3: Firecrawl returns GST but no turnover -> Crawl4AI runs only for turnover."""
        service, _firecrawl, crawl4ai = _service(
            [FirecrawlSearchResult(
                url="https://directory.example/ambica",
                title="Ambica Electro Control GSTIN",
                markdown="Ambica Electro Control GSTIN: 27AAPFU0939F1ZV",
            )],
            crawl4ai_page=Crawl4AIPage(url=WEBSITE, success=True, content=f"{COMPANY} Annual turnover: Rs. 50 Crore"),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        self.assertEqual(result.turnover.value, "50 Crore")
        crawl4ai.crawl.assert_called_once_with(WEBSITE)

    def test_turnover_found_gst_missing_calls_crawl4ai_only_for_gst(self, _validate):
        """Test 4: Firecrawl returns turnover but no GST -> Crawl4AI runs only for GST."""
        service, _firecrawl, crawl4ai = _service(
            [FirecrawlSearchResult(
                url="https://directory.example/ambica",
                title="Ambica Electro Control turnover",
                markdown="Ambica Electro Control Annual turnover: Rs. 50 Crore",
            )],
            crawl4ai_page=Crawl4AIPage(url=WEBSITE, success=True, content=f"{COMPANY} GSTIN: 27AAPFU0939F1ZV"),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.turnover.value, "50 Crore")
        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        crawl4ai.crawl.assert_called_once_with(WEBSITE)

    def test_empty_firecrawl_content_triggers_crawl4ai(self, _validate):
        """Test 5: Firecrawl returns a result with no usable content -> Crawl4AI fallback executes."""
        service, _firecrawl, crawl4ai = _service(
            [FirecrawlSearchResult(url="https://directory.example/ambica", title=COMPANY, description="")],
            crawl4ai_page=Crawl4AIPage(
                url=WEBSITE, success=True,
                content=f"{COMPANY} GSTIN: 27AAPFU0939F1ZV\nAnnual turnover: Rs. 50 Crore",
            ),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        self.assertTrue(crawl4ai.crawl.called)

    def test_both_firecrawl_and_crawl4ai_fail_returns_not_found_without_crashing(self, _validate):
        """Test 6: Both providers fail -> existing not_found result, no exception."""
        service, _firecrawl, crawl4ai = _service(
            [], crawl4ai_page=Crawl4AIPage(url=WEBSITE, error="crawl failed", success=False),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.status, "not_found")
        self.assertEqual(result.turnover.status, "not_found")

    @patch("services.gst_turnover_enrichment.service.settings.CRAWL4AI_ENABLED", False)
    def test_crawl4ai_disabled_keeps_existing_firecrawl_behavior(self, _validate):
        """Test 7: Crawl4AI disabled -> Firecrawl-only behavior, Crawl4AI never invoked."""
        service, _firecrawl, crawl4ai = _service(
            [],
            crawl4ai_page=Crawl4AIPage(url=WEBSITE, success=True, content=f"{COMPANY} GSTIN: 27AAPFU0939F1ZV"),
        )
        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.gst.status, "not_found")
        self.assertEqual(result.turnover.status, "not_found")
        crawl4ai.crawl.assert_not_called()

    def test_no_known_website_falls_back_to_duckduckgo_query_search(self, _validate):
        """No website at all -> Crawl4AI skips the website tier entirely and
        crawls DuckDuckGo for the same queries Firecrawl already builds."""
        def crawl_side_effect(url):
            if "duckduckgo.com" in url:
                return Crawl4AIPage(url=url, success=True, content=f'{COMPANY} GSTIN: 27AAPFU0939F1ZV')
            return Crawl4AIPage(url=url, success=False, error="unexpected url")

        service, _firecrawl, crawl4ai = _service([])
        crawl4ai.crawl.side_effect = crawl_side_effect

        result = service.resolve(COMPANY, None)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        called_url = crawl4ai.crawl.call_args_list[0].args[0]
        self.assertIn("html.duckduckgo.com/html", called_url)
        self.assertIn("GST", called_url)

    def test_website_crawl_empty_falls_back_to_duckduckgo_query_search(self, _validate):
        """Website is known but crawling it finds nothing -> falls back
        further to the DuckDuckGo query tier instead of stopping there."""
        def crawl_side_effect(url):
            if url == WEBSITE:
                return Crawl4AIPage(url=url, success=True, content="Nothing relevant here.")
            if "duckduckgo.com" in url:
                return Crawl4AIPage(url=url, success=True, content=f'{COMPANY} Annual turnover: Rs. 50 Crore')
            return Crawl4AIPage(url=url, success=False, error="unexpected url")

        service, _firecrawl, crawl4ai = _service([])
        crawl4ai.crawl.side_effect = crawl_side_effect

        result = service.resolve(COMPANY, WEBSITE)

        self.assertEqual(result.turnover.value, "50 Crore")
        urls_tried = [call.args[0] for call in crawl4ai.crawl.call_args_list]
        self.assertIn(WEBSITE, urls_tried)
        self.assertTrue(any("duckduckgo.com" in url for url in urls_tried))

    @patch("services.gst_turnover_enrichment.service.settings.ENRICHMENT_LOOKUP_TURNOVER", False)
    def test_stops_at_first_successful_duckduckgo_query(self, _validate):
        """The first query that yields a value stops the loop - later
        queries for the same field are never attempted. Turnover lookup is
        disabled so only the GST field's queries are exercised here."""
        calls = []

        def crawl_side_effect(url):
            calls.append(url)
            if "duckduckgo.com" in url and "GSTIN" not in url:
                # First GST query ("... GST") already succeeds.
                return Crawl4AIPage(url=url, success=True, content=f'{COMPANY} GSTIN: 27AAPFU0939F1ZV')
            return Crawl4AIPage(url=url, success=False, error="not reached")

        service, _firecrawl, crawl4ai = _service([])
        crawl4ai.crawl.side_effect = crawl_side_effect

        result = service.resolve(COMPANY, None)

        self.assertEqual(result.gst.value, "27AAPFU0939F1ZV")
        gst_query_urls = [url for url in calls if "duckduckgo.com" in url]
        self.assertEqual(len(gst_query_urls), 1)


if __name__ == "__main__":
    unittest.main()
