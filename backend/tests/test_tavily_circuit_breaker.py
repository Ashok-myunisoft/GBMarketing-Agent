import unittest
from unittest.mock import MagicMock, patch

import httpx

from services.tavily.tavily_search import TavilySearchService


class _FakeResponse:
    def __init__(self, status_code, json_payload=None, text=""):
        self.status_code = status_code
        self._json_payload = json_payload
        self.text = text

    def json(self):
        if self._json_payload is None:
            raise ValueError("no json")
        return self._json_payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=MagicMock(), response=self)


class TavilyCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        # Class-level state shared across every TavilySearchService instance -
        # must not leak between tests (or into other test files that also
        # instantiate TavilySearchService).
        TavilySearchService._unavailable_reason = None

    def tearDown(self):
        TavilySearchService._unavailable_reason = None

    @patch("services.tavily.tavily_search.httpx.Client")
    def test_quota_exceeded_trips_the_breaker(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = _FakeResponse(
            432, json_payload={"detail": {"error": "This request exceeds your plan's set usage limit."}}
        )
        mock_client_cls.return_value = mock_client

        service = TavilySearchService(api_key="tvly-dev-test")
        results = service.search("Example Pumps GST Number")

        self.assertEqual(results, [])
        self.assertFalse(service.is_configured)

    @patch("services.tavily.tavily_search.httpx.Client")
    def test_tripped_breaker_skips_the_network_entirely_for_new_instances(self, mock_client_cls):
        TavilySearchService._unavailable_reason = "This request exceeds your plan's set usage limit."

        # A brand new instance (as CompanyTavilyEnrichmentService creates its
        # own) must still see the breaker as tripped and never touch the
        # network.
        service = TavilySearchService(api_key="tvly-dev-test")
        results = service.search("Example Pumps GST Number")

        self.assertEqual(results, [])
        mock_client_cls.assert_not_called()

    @patch("services.tavily.tavily_search.httpx.Client")
    def test_ordinary_failure_does_not_trip_the_breaker(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = _FakeResponse(500, text="internal server error")
        mock_client_cls.return_value = mock_client

        service = TavilySearchService(api_key="tvly-dev-test")
        results = service.search("Example Pumps GST Number")

        self.assertEqual(results, [])
        self.assertTrue(service.is_configured)


if __name__ == "__main__":
    unittest.main()
