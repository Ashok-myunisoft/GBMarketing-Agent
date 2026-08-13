import unittest
from unittest.mock import MagicMock, patch

from services.contact_extraction.models import PageCategory
from services.crawler.html_crawler import CrawledPage
from services.enrichment.company_enrichment import CompanyTavilyEnrichmentService, TavilyEnrichmentResult
from services.extractor.schema import ExtractedCompanyRecord
from services.tavily.base import SearchProvider


class _ExplodingSearchProvider(SearchProvider):
    """Fails the test if the pipeline ever actually calls search() - proves
    the disabled/unconfigured short-circuit happens before any network call."""

    def search(self, query, max_results=None):
        raise AssertionError("Tavily search should not be called when the pipeline is a no-op")


class CompanyEnrichmentFallbackTests(unittest.TestCase):
    @patch("services.tavily.tavily_search.settings.TAVILY_API_KEY", None)
    def test_no_api_key_and_no_website_short_circuits_to_an_empty_result(self):
        """With no TAVILY_API_KEY and no known website, there is nothing to
        search with and nothing to crawl - this must be a true no-op."""
        service = CompanyTavilyEnrichmentService(browser=MagicMock())
        result = service.gather("Example Pumps Pvt Ltd", None)
        self.assertEqual(result, TavilyEnrichmentResult())
        self.assertTrue(result.is_empty())

    @patch("services.enrichment.company_enrichment.settings.ENRICHMENT_USE_TAVILY_PIPELINE", False)
    def test_disabled_flag_short_circuits_without_ever_searching(self):
        service = CompanyTavilyEnrichmentService(browser=MagicMock(), search_provider=_ExplodingSearchProvider())
        result = service.gather("Example Pumps Pvt Ltd")
        self.assertTrue(result.is_empty())

    @patch("services.tavily.tavily_search.settings.TAVILY_API_KEY", None)
    @patch("services.enrichment.company_enrichment.llm_extractor.extract")
    @patch("services.enrichment.company_enrichment.crawl")
    def test_known_website_still_gets_crawled_and_llm_extracted_without_tavily(
        self, mock_crawl, mock_extract
    ):
        """The real fix: a company with a known website must still get
        LLM-based extraction even when Tavily search can't run at all -
        Tavily is an accuracy multiplier, not a hard requirement for the LLM
        step."""
        mock_crawl.return_value = [
            CrawledPage(
                url="https://www.examplepumps.com",
                category=PageCategory.OTHER,
                text="Welcome to Example Pumps. Email: sales@examplepumps.com",
            )
        ]
        mock_extract.return_value = ExtractedCompanyRecord(
            email="sales@examplepumps.com", confidence={"email": 90}
        )

        service = CompanyTavilyEnrichmentService(browser=MagicMock())
        result = service.gather("Example Pumps Pvt Ltd", "https://www.examplepumps.com")

        mock_crawl.assert_called_once()
        mock_extract.assert_called_once()
        self.assertEqual(result.email, "sales@examplepumps.com")

    @patch("services.tavily.tavily_search.settings.TAVILY_API_KEY", None)
    @patch("services.enrichment.company_enrichment.llm_extractor.extract")
    @patch("services.enrichment.company_enrichment.crawl")
    def test_no_retry_attempted_when_tavily_is_unavailable_even_with_low_confidence(
        self, mock_crawl, mock_extract
    ):
        """Retrying only re-runs Tavily search with different wording - with
        no search provider available it would just re-crawl the identical
        site and ask the LLM the same question again for no possible gain."""
        mock_crawl.return_value = [
            CrawledPage(url="https://www.examplepumps.com", category=PageCategory.OTHER, text="some content")
        ]
        mock_extract.return_value = ExtractedCompanyRecord(
            contact_person="R. Kumar", confidence={"contact_person": 40}
        )

        service = CompanyTavilyEnrichmentService(browser=MagicMock())
        service.gather("Example Pumps Pvt Ltd", "https://www.examplepumps.com")

        mock_extract.assert_called_once()


if __name__ == "__main__":
    unittest.main()
