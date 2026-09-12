import unittest
from unittest.mock import MagicMock, patch

import httpx

from services.gst_turnover_enrichment.searxng_client import SearXNGSearchClient


def _client_returning(response):
    """Build a fake httpx.Client whose `with ... as client: client.get(...)`
    returns the given response, mirroring how SearXNGSearchClient uses it."""
    instance = MagicMock()
    instance.__enter__.return_value = instance
    instance.__exit__.return_value = False
    instance.get.return_value = response
    return instance


def _client_raising(exc):
    instance = MagicMock()
    instance.__enter__.return_value = instance
    instance.__exit__.return_value = False
    instance.get.side_effect = exc
    return instance


class SearXNGSearchClientTests(unittest.TestCase):
    def setUp(self):
        self.client = SearXNGSearchClient(base_url="http://searxng:8080", timeout_seconds=5)

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_successful_search_parses_results(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "results": [
                {"url": "https://a.example/1", "title": "A", "content": "snippet a", "score": 1.5, "engine": "google"},
                {"url": "https://b.example/2", "title": "B", "content": "snippet b", "score": 0.9, "engine": "bing"},
            ]
        }
        mock_client_cls.return_value = _client_returning(response)

        results = self.client.search("Ambica Electro Control GSTIN")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].url, "https://a.example/1")
        self.assertEqual(results[0].title, "A")
        self.assertEqual(results[0].description, "snippet a")
        self.assertEqual(results[0].score, 1.5)
        self.assertEqual(results[0].engine, "google")
        self.assertEqual(results[0].markdown, "")
        self.assertEqual(results[1].position, 1)

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_respects_limit(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "results": [{"url": f"https://x.example/{i}", "title": str(i)} for i in range(5)]
        }
        mock_client_cls.return_value = _client_returning(response)

        results = self.client.search("query", limit=2)

        self.assertEqual(len(results), 2)

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_empty_results_array_returns_empty_list(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {"results": []}
        mock_client_cls.return_value = _client_returning(response)

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_missing_results_key_returns_empty_list(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {"query": "query"}
        mock_client_cls.return_value = _client_returning(response)

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_timeout_returns_empty_list_without_raising(self, mock_client_cls):
        mock_client_cls.return_value = _client_raising(httpx.TimeoutException("timed out"))

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_connection_error_returns_empty_list_without_raising(self, mock_client_cls):
        mock_client_cls.return_value = _client_raising(httpx.ConnectError("refused"))

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_http_error_status_returns_empty_list(self, mock_client_cls):
        response = MagicMock(status_code=500)
        mock_client_cls.return_value = _client_returning(response)

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_malformed_json_returns_empty_list(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.side_effect = ValueError("not json")
        mock_client_cls.return_value = _client_returning(response)

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_non_dict_json_body_returns_empty_list(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = ["not", "a", "dict"]
        mock_client_cls.return_value = _client_returning(response)

        self.assertEqual(self.client.search("query"), [])

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_result_items_missing_url_are_skipped(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "results": [{"title": "no url here"}, {"url": "https://a.example/1", "title": "has url"}]
        }
        mock_client_cls.return_value = _client_returning(response)

        results = self.client.search("query")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://a.example/1")

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_non_numeric_score_defaults_to_zero(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {"results": [{"url": "https://a.example/1", "score": "not-a-number"}]}
        mock_client_cls.return_value = _client_returning(response)

        results = self.client.search("query")

        self.assertEqual(results[0].score, 0.0)

    def test_not_configured_skips_request_entirely(self):
        client = SearXNGSearchClient(base_url="", timeout_seconds=5)

        with patch("services.gst_turnover_enrichment.searxng_client.httpx.Client") as mock_client_cls:
            results = client.search("query")

        self.assertEqual(results, [])
        mock_client_cls.assert_not_called()

    def test_blank_query_returns_empty_list_without_request(self):
        with patch("services.gst_turnover_enrichment.searxng_client.httpx.Client") as mock_client_cls:
            results = self.client.search("   ")

        self.assertEqual(results, [])
        mock_client_cls.assert_not_called()

    def test_is_configured_reflects_base_url(self):
        self.assertTrue(SearXNGSearchClient(base_url="http://searxng:8080").is_configured)
        self.assertFalse(SearXNGSearchClient(base_url="").is_configured)

    @patch("services.gst_turnover_enrichment.searxng_client.httpx.Client")
    def test_trailing_slash_on_base_url_is_stripped(self, mock_client_cls):
        response = MagicMock(status_code=200)
        response.json.return_value = {"results": []}
        fake_client = _client_returning(response)
        mock_client_cls.return_value = fake_client

        client = SearXNGSearchClient(base_url="http://searxng:8080/", timeout_seconds=5)
        client.search("query")

        called_url = fake_client.get.call_args.args[0]
        self.assertEqual(called_url, "http://searxng:8080/search")


if __name__ == "__main__":
    unittest.main()
