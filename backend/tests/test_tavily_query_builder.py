import unittest

from services.tavily.query_builder import build_initial_queries, build_refined_queries


class QueryBuilderTests(unittest.TestCase):
    def test_initial_queries_cover_the_expected_variations(self):
        queries = build_initial_queries("Example Pumps Pvt Ltd")
        self.assertIn("Example Pumps Pvt Ltd official website", queries)
        self.assertIn("Example Pumps Pvt Ltd managing director", queries)
        self.assertIn("Example Pumps Pvt Ltd leadership", queries)
        self.assertEqual(len(queries), 3)

    def test_initial_queries_exclude_gst_turnover_terms(self):
        # GST/turnover discovery no longer goes through Tavily at all (see
        # services/gst_turnover_enrichment - its own SearXNG/Crawl4AI path).
        queries = " ".join(build_initial_queries("Example Pumps Pvt Ltd"))
        for term in ("GSTIN", "GST", "Turnover", "Revenue", "Annual Report"):
            self.assertNotIn(term, queries)

    def test_refined_queries_are_a_smaller_targeted_set(self):
        initial = build_initial_queries("Example Pumps Pvt Ltd")
        refined = build_refined_queries("Example Pumps Pvt Ltd")
        self.assertIn("Example Pumps Pvt Ltd Managing Director", refined)
        self.assertLess(len(refined), len(initial))


if __name__ == "__main__":
    unittest.main()
