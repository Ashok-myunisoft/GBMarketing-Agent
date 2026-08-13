import unittest

from services.tavily.base import SearchResult
from services.tavily.source_ranker import rank_and_filter


class SourceRankerTests(unittest.TestCase):
    def test_official_website_outranks_directory_and_social(self):
        results = [
            SearchResult(
                url="https://facebook.com/examplepumps",
                title="Example Pumps on Facebook",
                content="Example Pumps",
            ),
            SearchResult(
                url="https://www.indiamart.com/example-pumps",
                title="Example Pumps - IndiaMART",
                content="Example Pumps supplier",
            ),
            SearchResult(
                url="https://www.examplepumps.com/about",
                title="About Example Pumps",
                content="Example Pumps is a manufacturer",
            ),
        ]
        ranked = rank_and_filter(results, "Example Pumps Pvt Ltd")
        self.assertEqual(ranked[0].url, "https://www.examplepumps.com/about")
        self.assertEqual(ranked[-1].url, "https://facebook.com/examplepumps")

    def test_annual_report_hint_ranks_as_high_priority(self):
        results = [
            SearchResult(
                url="https://www.justdial.com/example-pumps",
                title="Example Pumps",
                content="Example Pumps listing",
            ),
            SearchResult(
                url="https://www.bseindia.com/example-pumps-annual-report.pdf",
                title="Example Pumps Annual Report",
                content="Example Pumps FY24 annual report",
            ),
        ]
        ranked = rank_and_filter(results, "Example Pumps Pvt Ltd")
        self.assertEqual(ranked[0].url, "https://www.bseindia.com/example-pumps-annual-report.pdf")

    def test_irrelevant_domains_are_dropped(self):
        results = [
            SearchResult(url="https://www.naukri.com/example-pumps-jobs", title="Jobs at Example Pumps", content="hiring"),
            SearchResult(url="https://www.examplepumps.com", title="Example Pumps", content="Example Pumps homepage"),
        ]
        ranked = rank_and_filter(results, "Example Pumps Pvt Ltd")
        self.assertEqual([r.url for r in ranked], ["https://www.examplepumps.com"])

    def test_dedupes_by_normalized_url(self):
        results = [
            SearchResult(url="https://www.examplepumps.com/", title="Example Pumps", content="Example Pumps"),
            SearchResult(url="https://www.examplepumps.com", title="Example Pumps", content="Example Pumps"),
        ]
        ranked = rank_and_filter(results, "Example Pumps Pvt Ltd")
        self.assertEqual(len(ranked), 1)


if __name__ == "__main__":
    unittest.main()
