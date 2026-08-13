import unittest
from unittest.mock import MagicMock, patch

import httpx

import services.gst_turnover_enrichment.firecrawl_cloud_search as firecrawl_cloud_search_module
from services.gst_turnover_enrichment.firecrawl_cloud_search import (
    FIRECRAWL_CLOUD_SEARCH_URL,
    FirecrawlCloudSearchClient,
    FirecrawlCloudSearchError,
)


def _mock_client(response=None, raise_error=None):
    """Build a mock replacing ``httpx.Client`` used as a context manager."""
    instance = MagicMock()
    if raise_error is not None:
        instance.post.side_effect = raise_error
    else:
        instance.post.return_value = response
    factory = MagicMock()
    factory.return_value.__enter__.return_value = instance
    factory.return_value.__exit__.return_value = False
    return factory, instance


def _response(status_code=200, json_body=None, raise_for_json=None):
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    if raise_for_json is not None:
        response.json.side_effect = raise_for_json
    else:
        response.json.return_value = json_body
    return response


class FirecrawlCloudSearchClientTests(unittest.TestCase):
    def test_missing_api_key_raises_without_any_network_call(self):
        # "" is a deliberate "no key" override, distinct from None (which
        # falls back to settings.FIRECRAWL_API_KEY - set in this repo's .env).
        client = FirecrawlCloudSearchClient(api_key="")
        self.assertFalse(client.is_configured)
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("Ambica Electro Control GSTIN")
        self.assertFalse(ctx.exception.retryable)

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_successful_search_parses_web_results(self, mock_client_cls):
        body = {
            "success": True,
            "data": {
                "web": [
                    {
                        "url": "https://gst.jamku.app/gstin/29CDGPD3282E1ZO",
                        "title": "AMBICA ELECTRO CONTROL 29CDGPD3282E1ZO GST ...",
                        "description": "GST return filing status of Ambica electro control having gstin 29CDGPD3282E1ZO",
                        "position": 1,
                    }
                ]
            },
            "creditsUsed": 2,
            "id": "abc",
        }
        factory, instance = _mock_client(response=_response(200, body))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        results = client.search('"Ambica Electro Control" GSTIN', limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://gst.jamku.app/gstin/29CDGPD3282E1ZO")
        self.assertEqual(results[0].position, 1)
        posted_url = instance.post.call_args.args[0]
        headers = instance.post.call_args.kwargs["headers"]
        self.assertEqual(posted_url, FIRECRAWL_CLOUD_SEARCH_URL)
        self.assertEqual(headers["Authorization"], "Bearer fc-test-key")

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_success_false_raises_non_retryable_error(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(200, {"success": False, "error": "bad query"}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("query")
        self.assertFalse(ctx.exception.retryable)

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_missing_data_key_returns_empty_results(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(200, {"success": True}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        self.assertEqual(client.search("query"), [])

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_missing_web_key_returns_empty_results(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(200, {"success": True, "data": {}}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        self.assertEqual(client.search("query"), [])

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_empty_web_list_returns_empty_results(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(200, {"success": True, "data": {"web": []}}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        self.assertEqual(client.search("query"), [])

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_invalid_api_key_raises_non_retryable_error(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(401, {"error": "Unauthorized"}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-bad-key")
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("query")
        self.assertEqual(ctx.exception.reason, "invalid_api_key")
        self.assertFalse(ctx.exception.retryable)

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_rate_limit_raises_retryable_error(self, mock_client_cls):
        factory, _ = _mock_client(response=_response(429, {"error": "Too Many Requests"}))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("query")
        self.assertEqual(ctx.exception.reason, "rate_limited")
        self.assertTrue(ctx.exception.retryable)

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_timeout_raises_retryable_error(self, mock_client_cls):
        factory, _ = _mock_client(raise_error=httpx.TimeoutException("timed out"))
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("query")
        self.assertEqual(ctx.exception.reason, "timeout")
        self.assertTrue(ctx.exception.retryable)

    @patch("services.gst_turnover_enrichment.firecrawl_cloud_search.httpx.Client")
    def test_malformed_json_raises_non_retryable_error(self, mock_client_cls):
        response = _response(200, raise_for_json=ValueError("not json"))
        factory, _ = _mock_client(response=response)
        mock_client_cls.side_effect = factory

        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        with self.assertRaises(FirecrawlCloudSearchError) as ctx:
            client.search("query")
        self.assertEqual(ctx.exception.reason, "malformed_response")
        self.assertFalse(ctx.exception.retryable)

    def test_unset_firecrawl_api_key_env_is_treated_as_not_configured(self):
        with patch.object(firecrawl_cloud_search_module.settings, "FIRECRAWL_API_KEY", None):
            client = FirecrawlCloudSearchClient()
            self.assertFalse(client.is_configured)
            with self.assertRaises(FirecrawlCloudSearchError):
                client.search("query")

    def test_empty_query_returns_empty_results_without_network_call(self):
        client = FirecrawlCloudSearchClient(api_key="fc-test-key")
        self.assertEqual(client.search("   "), [])


if __name__ == "__main__":
    unittest.main()
