import unittest
from unittest.mock import MagicMock, patch

import services.gst_turnover_enrichment.firecrawl_client as firecrawl_client_module
from services.gst_turnover_enrichment.firecrawl_client import FirecrawlClient, extract_links
from services.gst_turnover_enrichment.firecrawl_cloud_search import (
    CloudSearchResult,
    FirecrawlCloudSearchError,
)


def _document(html="<html></html>", markdown="text"):
    doc = MagicMock()
    doc.html = html
    doc.raw_html = None
    doc.markdown = markdown
    return doc


class FirecrawlClientTests(unittest.TestCase):
    def setUp(self):
        # The success cache is process-global (shared across EnrichmentAgent
        # workers by design) - clear it so one test's cached URL can't mask
        # another test's mocked response.
        firecrawl_client_module._GLOBAL_CACHE.clear()

    def test_unconfigured_without_an_api_url(self):
        # api_url="" is a deliberate override, distinct from None (which
        # would fall back to settings.FIRECRAWL_API_URL).
        client = FirecrawlClient(api_key=None, api_url="")
        self.assertFalse(client.is_configured)
        page = client.scrape("https://example.com")
        self.assertFalse(page.success)
        self.assertIsNotNone(page.error)

    @patch("services.gst_turnover_enrichment.firecrawl_client.Firecrawl")
    def test_self_hosted_is_configured_without_an_api_key(self, mock_firecrawl_cls):
        # Self-hosted Firecrawl needs no API key - only a reachable api_url.
        # api_key=None must stay None here, not fall back to whatever cloud
        # key happens to be configured in the environment.
        mock_firecrawl_cls.return_value.scrape.return_value = _document()

        with patch.object(firecrawl_client_module.settings, "FIRECRAWL_API_KEY", None):
            client = FirecrawlClient(api_key=None, api_url="http://localhost:3002")
            self.assertTrue(client.is_configured)

            page = client.scrape("https://example.com")
            self.assertTrue(page.success)

        _, kwargs = mock_firecrawl_cls.call_args
        self.assertIsNone(kwargs.get("api_key"))
        self.assertEqual(kwargs.get("api_url"), "http://localhost:3002")

    @patch("services.gst_turnover_enrichment.firecrawl_client.Firecrawl")
    def test_successful_scrape_returns_html_and_markdown(self, mock_firecrawl_cls):
        mock_firecrawl_cls.return_value.scrape.return_value = _document(html="<p>hi</p>", markdown="hi")

        client = FirecrawlClient(api_key="test-key", api_url="http://localhost:3002")
        page = client.scrape("https://example.com")

        self.assertTrue(page.success)
        self.assertEqual(page.html, "<p>hi</p>")
        self.assertEqual(page.markdown, "hi")

    @patch("services.gst_turnover_enrichment.firecrawl_client.Firecrawl")
    def test_repeated_scrape_of_the_same_url_is_cached(self, mock_firecrawl_cls):
        mock_firecrawl_cls.return_value.scrape.return_value = _document()

        client = FirecrawlClient(api_key="test-key", api_url="http://localhost:3002")
        client.scrape("https://example.com")
        client.scrape("https://example.com/")  # trailing slash, same page

        self.assertEqual(mock_firecrawl_cls.return_value.scrape.call_count, 1)

    @patch("services.gst_turnover_enrichment.firecrawl_client.Firecrawl")
    def test_scrape_failure_degrades_to_an_unsuccessful_page_without_raising(self, mock_firecrawl_cls):
        mock_firecrawl_cls.return_value.scrape.side_effect = RuntimeError("boom")

        client = FirecrawlClient(api_key="test-key", api_url="http://localhost:3002")
        page = client.scrape("https://example.com")

        self.assertFalse(page.success)
        self.assertIn("boom", page.error)
        self.assertEqual(page.error_code, "SCRAPE_FAILED")

    @patch("services.gst_turnover_enrichment.firecrawl_client.Firecrawl")
    def test_anti_bot_failure_is_classified_distinctly_and_not_retried(self, mock_firecrawl_cls):
        mock_firecrawl_cls.return_value.scrape.side_effect = Exception(
            "Scrape aborted after exceeding retry limit (document_antibot)."
        )

        client = FirecrawlClient(api_key=None, api_url="http://localhost:3002")
        page = client.scrape("https://indiamart.com/some-listing")

        self.assertFalse(page.success)
        self.assertEqual(page.error_code, "ANTI_BOT")
        self.assertEqual(mock_firecrawl_cls.return_value.scrape.call_count, 1)


class FirecrawlClientSearchTests(unittest.TestCase):
    """search() must go through Firecrawl Cloud, never the self-hosted
    instance used by scrape() - even when both are configured."""

    def setUp(self):
        firecrawl_client_module._GLOBAL_CACHE.clear()
        firecrawl_client_module._CLOUD_SEARCH_PAUSED_UNTIL = 0.0

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_search_uses_cloud_client_not_self_hosted_sdk(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = True
        mock_cloud_cls.return_value.search.return_value = [
            CloudSearchResult(url="https://gst.jamku.app/gstin/29CDGPD3282E1ZO",
                               title="AMBICA ELECTRO CONTROL 29CDGPD3282E1ZO GST ...",
                               description="GST return filing status ... gstin 29CDGPD3282E1ZO", position=1)
        ]

        # api_url="" keeps the self-hosted scrape client unconfigured/unused,
        # proving search() does not depend on it at all.
        client = FirecrawlClient(api_key="fc-test-key", api_url="")
        results = client.search('"Ambica Electro Control" GSTIN', limit=10, company="Ambica Electro Control")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://gst.jamku.app/gstin/29CDGPD3282E1ZO")
        mock_cloud_cls.return_value.search.assert_called_once_with('"Ambica Electro Control" GSTIN', limit=10)

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_search_returns_empty_list_when_api_key_missing(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = False

        client = FirecrawlClient(api_key=None, api_url="")
        results = client.search("Ambica Electro Control GSTIN")

        self.assertEqual(results, [])
        mock_cloud_cls.return_value.search.assert_not_called()

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_search_retries_a_retryable_failure_then_succeeds(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = True
        mock_cloud_cls.return_value.search.side_effect = [
            FirecrawlCloudSearchError("timeout", retryable=True),
            [CloudSearchResult(url="https://example.com/a", title="t", description="d", position=1)],
        ]

        with patch.object(firecrawl_client_module.settings, "FIRECRAWL_SEARCH_MAX_RETRIES", 1):
            client = FirecrawlClient(api_key="fc-test-key", api_url="")
            results = client.search("query")

        self.assertEqual(len(results), 1)
        self.assertEqual(mock_cloud_cls.return_value.search.call_count, 2)

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_search_does_not_retry_a_non_retryable_failure(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = True
        mock_cloud_cls.return_value.search.side_effect = FirecrawlCloudSearchError("invalid_api_key", retryable=False)

        with patch.object(firecrawl_client_module.settings, "FIRECRAWL_SEARCH_MAX_RETRIES", 2):
            client = FirecrawlClient(api_key="fc-bad-key", api_url="")
            results = client.search("query")

        self.assertEqual(results, [])
        self.assertEqual(mock_cloud_cls.return_value.search.call_count, 1)

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_quota_failure_pauses_later_cloud_searches(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = True
        mock_cloud_cls.return_value.search.side_effect = FirecrawlCloudSearchError("http_402", retryable=False)

        client = FirecrawlClient(api_key="fc-no-credit", api_url="")
        self.assertEqual(client.search("first query"), [])
        self.assertEqual(client.search("later query"), [])

        self.assertEqual(mock_cloud_cls.return_value.search.call_count, 1)

    @patch("services.gst_turnover_enrichment.firecrawl_client.FirecrawlCloudSearchClient")
    def test_search_gives_up_after_exhausting_retries(self, mock_cloud_cls):
        mock_cloud_cls.return_value.is_configured = True
        mock_cloud_cls.return_value.search.side_effect = FirecrawlCloudSearchError("timeout", retryable=True)

        with patch.object(firecrawl_client_module.settings, "FIRECRAWL_SEARCH_MAX_RETRIES", 1):
            client = FirecrawlClient(api_key="fc-test-key", api_url="")
            results = client.search("query")

        self.assertEqual(results, [])
        self.assertEqual(mock_cloud_cls.return_value.search.call_count, 2)

    def test_empty_query_returns_empty_list_without_constructing_a_request(self):
        client = FirecrawlClient(api_key="fc-test-key", api_url="")
        self.assertEqual(client.search("   "), [])


class ExtractLinksTests(unittest.TestCase):
    def test_extracts_absolute_urls_and_labels(self):
        html = '<html><body><a href="/about">About Us</a></body></html>'
        links = extract_links(html, "https://example.com")
        self.assertEqual(links, [("https://example.com/about", "About Us")])

    def test_ignores_anchor_mailto_and_javascript_links(self):
        html = (
            '<html><body>'
            '<a href="#top">Top</a>'
            '<a href="mailto:info@example.com">Email</a>'
            '<a href="javascript:void(0)">JS</a>'
            '</body></html>'
        )
        self.assertEqual(extract_links(html, "https://example.com"), [])

    def test_empty_html_returns_no_links(self):
        self.assertEqual(extract_links("", "https://example.com"), [])


if __name__ == "__main__":
    unittest.main()
