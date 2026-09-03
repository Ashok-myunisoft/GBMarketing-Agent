import unittest
from unittest.mock import MagicMock, patch

from services.gst_turnover_enrichment.crawl4ai_client import Crawl4AIClient, Crawl4AIPage


class Crawl4AIClientTests(unittest.TestCase):
    def test_active_event_loop_uses_a_worker_thread(self):
        client = Crawl4AIClient()
        expected = Crawl4AIPage(url="https://example.com", content="ok", success=True)

        with patch(
            "services.gst_turnover_enrichment.crawl4ai_client.asyncio.get_running_loop",
            return_value=MagicMock(),
        ), patch.object(client, "_crawl_in_worker_thread", return_value=expected) as worker:
            page = client.crawl("https://example.com")

        self.assertIs(page, expected)
        worker.assert_called_once_with("https://example.com")


if __name__ == "__main__":
    unittest.main()
