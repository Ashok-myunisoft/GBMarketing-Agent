import unittest

from services.gst_turnover_enrichment.gst_extraction import find_valid_gstin, find_valid_gstins
from services.gst_turnover_enrichment.website_source import _candidate_urls
from services.gst_turnover_enrichment.pdf_utils import same_site_pdf_links, is_annual_report
from services.gst_turnover_enrichment.turnover_extraction import find_turnover_candidates


class GstTurnoverEnrichmentTests(unittest.TestCase):
    def test_find_valid_gstin_accepts_checksum_valid_value(self):
        html = "Company GSTIN: 27AAPFU0939F1ZV"
        self.assertEqual(find_valid_gstin(html), "27AAPFU0939F1ZV")

    def test_find_valid_gstin_rejects_invalid_checksum(self):
        html = "Company GSTIN: 27AAACI1503H1Z"
        self.assertIsNone(find_valid_gstin(html))

    def test_find_valid_gstins_retains_multiple_valid_candidates_for_ranking(self):
        html = "GSTINs: 27AAPFU0939F1ZV; 33AAPFU0939F1Z2"
        self.assertEqual(find_valid_gstins(html), ["27AAPFU0939F1ZV", "33AAPFU0939F1Z2"])

    def test_turnover_candidates_keep_latest_financial_year_and_metric(self):
        text = "Revenue From Operations\nFY 2023-24: Rs. 80 Crore\nFY 2024-25: Rs. 128 Crore"
        candidates = find_turnover_candidates(text)
        self.assertEqual(candidates[0]["value"], "128 Crore")
        self.assertEqual(candidates[0]["financial_year"], "2024-25")
        self.assertEqual(candidates[0]["metric"], "Revenue From Operations")

    def test_candidate_urls_picks_gst_and_registration_pages(self):
        html = '<html><body><a href="/gst-registration">GST Registration</a></body></html>'
        urls = _candidate_urls(html, "https://example.com", limit=5)
        self.assertEqual(urls, ["https://example.com/gst-registration"])

    def test_candidate_urls_ignores_off_site_links(self):
        html = '<html><body><a href="https://other.com/gst-registration">GST Registration</a></body></html>'
        urls = _candidate_urls(html, "https://example.com", limit=5)
        self.assertEqual(urls, [])

    def test_same_site_pdf_links_prioritizes_gst_certificate(self):
        html = '<html><body><a href="/docs/gst-certificate.pdf">GST Certificate</a></body></html>'
        result = same_site_pdf_links(html, "https://example.com", limit=5)
        self.assertEqual(result[0][0], "https://example.com/docs/gst-certificate.pdf")
        self.assertTrue(is_annual_report(result[0][1]) or "gst" in result[0][1])

    def test_same_site_pdf_links_ignores_non_pdf_links(self):
        html = '<html><body><a href="/about">About</a></body></html>'
        result = same_site_pdf_links(html, "https://example.com", limit=5)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
