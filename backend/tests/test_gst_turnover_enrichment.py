import unittest

from services.gst_turnover_enrichment.gst_extraction import (
    find_valid_gstin,
    find_valid_gstins,
    find_valid_gstins_with_evidence,
)
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

    def test_turnover_candidate_carries_an_evidence_snippet(self):
        text = "Total Revenue: Rs 120 Crore for FY 2023-24"
        candidates = find_turnover_candidates(text)
        self.assertEqual(candidates[0]["evidence"], text)

    def test_bare_profit_figure_is_not_mistaken_for_turnover(self):
        text = "Net Profit: Rs 15 Crore for FY 2023-24"
        self.assertEqual(find_turnover_candidates(text), [])

    def test_ebitda_and_net_worth_are_not_mistaken_for_turnover(self):
        self.assertEqual(find_turnover_candidates("EBITDA: Rs 20 Crore"), [])
        self.assertEqual(find_turnover_candidates("Net Worth: Rs 200 Crore"), [])

    def test_investment_and_production_capacity_are_not_mistaken_for_turnover(self):
        self.assertEqual(find_turnover_candidates("Total Investment: Rs 50 Crore"), [])
        self.assertEqual(find_turnover_candidates("Production Capacity: 10000 MT"), [])

    def test_turnover_far_from_an_unrelated_profit_line_is_still_found(self):
        # Profit mentioned several lines away (outside the +-2 line window)
        # must not suppress a genuinely separate, legitimate turnover figure.
        text = "\n".join([
            "Total Revenue: Rs 120 Crore",
            "line filler 1", "line filler 2", "line filler 3",
            "Net Profit: Rs 15 Crore",
        ])
        candidates = find_turnover_candidates(text)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["value"], "120 Crore")

    def test_gstin_evidence_captures_surrounding_text_per_value(self):
        text = (
            "Ambica Electro Control Pvt Ltd\n"
            "GSTIN: 27AAPFU0939F1ZV\n"
            "Registered address: Pune, Maharashtra\n"
            "\n"
            "Supplier GSTIN: 33AAPFU0939F1Z2\n"
        )
        results = dict(find_valid_gstins_with_evidence(text))
        self.assertEqual(set(results), {"27AAPFU0939F1ZV", "33AAPFU0939F1Z2"})
        self.assertIn("Ambica Electro Control", results["27AAPFU0939F1ZV"])
        self.assertIn("Supplier", results["33AAPFU0939F1Z2"])

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
