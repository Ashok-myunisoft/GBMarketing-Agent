import sys
import types
import unittest
from unittest.mock import MagicMock

fake_playwright = types.ModuleType('playwright')
fake_playwright_sync = types.ModuleType('playwright.sync_api')
fake_playwright_sync.sync_playwright = lambda *args, **kwargs: None
fake_playwright_sync.Playwright = object
fake_playwright_sync.Browser = object
fake_playwright_sync.BrowserContext = object
fake_playwright_sync.Page = object
fake_playwright_sync.Error = Exception
fake_playwright_sync.TimeoutError = Exception
sys.modules['playwright'] = fake_playwright
sys.modules['playwright.sync_api'] = fake_playwright_sync

from services.crawler.html_crawler import crawl, fetch_single_page


def _make_page(url, pdf_href="/docs/gst-certificate.pdf", pdf_label="GST Certificate"):
    page = MagicMock()
    page.url = url
    page.title.return_value = "Example Pumps"
    # _read_pdf_links now parses this HTML directly (via BeautifulSoup, see
    # pdf_utils.same_site_pdf_links) instead of a Playwright locator, so the
    # PDF link has to actually be present in the returned markup.
    page.content.return_value = f'<html><body><a href="{pdf_href}">{pdf_label}</a></body></html>'

    body_locator = MagicMock()
    body_locator.inner_text.return_value = "Welcome to Example Pumps"

    anchor_locator = MagicMock()
    anchor_locator.count.return_value = 0  # no internal links to discover

    def locator_side_effect(selector):
        if selector == "body":
            return body_locator
        if selector == "a[href]":
            return anchor_locator
        return MagicMock(count=MagicMock(return_value=0))

    page.locator.side_effect = locator_side_effect
    return page


class HtmlCrawlerPdfDiscoveryTests(unittest.TestCase):
    def test_crawl_captures_pdf_links_found_on_the_page(self):
        # A company's own GST certificate/annual report PDF, linked from its
        # homepage, must be discoverable even when Tavily never surfaced it
        # directly - this is what lets the LLM extractor see it at all in a
        # Tavily-unavailable degraded run.
        page = _make_page("https://www.examplepumps.com")
        browser = MagicMock()
        browser.new_context.return_value = MagicMock()
        browser.new_page.return_value = page

        pages = crawl(browser, "https://www.examplepumps.com", max_pages=1)

        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0].pdf_links), 1)
        pdf_url, label = pages[0].pdf_links[0]
        self.assertEqual(pdf_url, "https://www.examplepumps.com/docs/gst-certificate.pdf")
        self.assertIn("gst certificate", label)

    def test_fetch_single_page_captures_pdf_links_too(self):
        page = _make_page("https://www.indiamart.com/example-pumps")
        browser = MagicMock()
        browser.new_context.return_value = MagicMock()
        browser.new_page.return_value = page

        result = fetch_single_page(browser, "https://www.indiamart.com/example-pumps")

        self.assertIsNotNone(result)
        self.assertEqual(len(result.pdf_links), 1)
        self.assertIn("gst-certificate.pdf", result.pdf_links[0][0])


if __name__ == "__main__":
    unittest.main()
