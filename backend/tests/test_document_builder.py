import unittest

from services.contact_extraction.models import PageCategory
from services.crawler.document_builder import build_document
from services.crawler.html_crawler import CrawledPage
from services.crawler.pdf_crawler import CrawledPdf


class DocumentBuilderTests(unittest.TestCase):
    def test_sections_use_expected_headers(self):
        pages = [
            CrawledPage(url="https://example.com", category=PageCategory.OTHER, text="Welcome to Example Pumps"),
            CrawledPage(
                url="https://example.com/leadership",
                category=PageCategory.LEADERSHIP,
                text="R. Kumar, Managing Director",
            ),
        ]
        pdfs = [CrawledPdf(url="https://example.com/gst.pdf", category="gst_certificate", text="GSTIN: 33ABCDE1234F1Z5")]

        document = build_document(pages, pdfs, max_chars=10_000)

        self.assertIn("===== HOME (https://example.com) =====", document)
        self.assertIn("===== LEADERSHIP (https://example.com/leadership) =====", document)
        self.assertIn("===== GST CERTIFICATE (https://example.com/gst.pdf) =====", document)
        self.assertIn("R. Kumar, Managing Director", document)

    def test_blank_pages_are_skipped(self):
        pages = [CrawledPage(url="https://example.com", category=PageCategory.OTHER, text="   ")]
        document = build_document(pages, [], max_chars=10_000)
        self.assertEqual(document, "")

    def test_truncates_to_max_chars_dropping_lowest_priority_first(self):
        pages = [
            CrawledPage(url="https://example.com", category=PageCategory.OTHER, text="x" * 500),
            CrawledPage(url="https://example.com/leadership", category=PageCategory.LEADERSHIP, text="y" * 500),
        ]
        document = build_document(pages, [], max_chars=400)
        # The higher-priority LEADERSHIP section is kept; the generic HOME
        # section is dropped first when the budget can't fit both.
        self.assertIn("LEADERSHIP", document)
        self.assertNotIn("HOME", document)


if __name__ == "__main__":
    unittest.main()
