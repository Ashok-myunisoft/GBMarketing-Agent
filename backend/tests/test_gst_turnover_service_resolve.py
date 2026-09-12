import sys
import types
import unittest
from unittest.mock import MagicMock, patch

fake_playwright = types.ModuleType("playwright")
fake_playwright_sync = types.ModuleType("playwright.sync_api")
fake_playwright_sync.sync_playwright = lambda *args, **kwargs: None
fake_playwright_sync.Playwright = object
fake_playwright_sync.Browser = object
fake_playwright_sync.BrowserContext = object
fake_playwright_sync.Page = object
fake_playwright_sync.Error = Exception
fake_playwright_sync.TimeoutError = Exception
sys.modules["playwright"] = fake_playwright
sys.modules["playwright.sync_api"] = fake_playwright_sync

from services.gst_turnover_enrichment import confidence
from services.gst_turnover_enrichment.models import SourceCandidate
from services.gst_turnover_enrichment.openai_research_client import AIResearchResult
from services.gst_turnover_enrichment.service import GstTurnoverEnrichmentService

VALID_GSTIN = "27AAPFU0939F1ZV"  # checksum-valid, used throughout


def _found_gst(value=VALID_GSTIN, sources=None):
    return AIResearchResult(
        status="found", value=value, evidence="matches company", confidence_label="high",
        sources=sources if sources is not None else ["https://directory.example/ambica"],
    )


def _found_turnover(value="50 Crore", sources=None):
    return AIResearchResult(
        status="found", value=value, currency="INR", financial_year="FY 2023-24", metric="revenue",
        evidence="annual report", confidence_label="high",
        sources=sources if sources is not None else ["https://directory.example/ambica"],
    )


_NOT_FOUND = AIResearchResult(status="not_found")


class ResolveTests(unittest.TestCase):
    def _service(self, gst_result=_NOT_FOUND, turnover_result=_NOT_FOUND):
        research = MagicMock()
        research.search_gst.return_value = gst_result
        research.search_turnover.return_value = turnover_result
        service = GstTurnoverEnrichmentService(browser=MagicMock(), research=research)
        return service, research

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_resolves_gst_and_turnover_from_ai_research(self, _validate):
        service, research = self._service(_found_gst(), _found_turnover())

        result = service.resolve(
            "Ambica Electro Control", "https://ambicaelectro.example",
            city="Coimbatore", state="Tamil Nadu", industry="Valves",
            official_name="Ambica Electro Control Pvt Ltd", address="123 Industrial Estate", cin="U12345TN2000PLC001",
        )

        self.assertEqual(result.gst.value, VALID_GSTIN)
        self.assertEqual(result.gst.status, "verified")
        self.assertEqual(result.turnover.value, "50 Crore")
        self.assertEqual(result.turnover.financial_year, "FY 2023-24")
        self.assertEqual(result.turnover.metric, "revenue")
        self.assertEqual(result.turnover.status, "verified")

        # The company's full context reaches the research client - it, not
        # Python, controls the search query via the prompt templates.
        gst_call_kwargs = research.search_gst.call_args.kwargs
        self.assertEqual(gst_call_kwargs["company_name"], "Ambica Electro Control")
        self.assertEqual(gst_call_kwargs["official_name"], "Ambica Electro Control Pvt Ltd")
        self.assertEqual(gst_call_kwargs["address"], "123 Industrial Estate")
        self.assertEqual(gst_call_kwargs["cin"], "U12345TN2000PLC001")

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_checksum_invalid_gstin_from_ai_is_rejected(self, _validate):
        # The last character is wrong - a plausible-looking but
        # checksum-invalid GSTIN, exactly the kind of hallucination the
        # model can produce - must never be trusted at face value.
        hallucinated = AIResearchResult(status="found", value="27AAPFU0939F1ZZ", sources=["https://example.com"])
        service, _research = self._service(gst_result=hallucinated)

        result = service.resolve("Ambica Electro Control", None)

        self.assertEqual(result.gst.status, "not_found")

    def test_ai_research_not_found_is_non_fatal(self):
        service, research = self._service()

        result = service.resolve("Ambica Electro Control", None)

        self.assertEqual(result.gst.status, "not_found")
        self.assertEqual(result.turnover.status, "not_found")
        research.search_gst.assert_called_once()
        research.search_turnover.assert_called_once()

    def test_already_known_gst_skips_ai_research_for_that_field(self):
        service, research = self._service(turnover_result=_found_turnover())

        service.resolve("Ambica Electro Control", None, gst=VALID_GSTIN)

        research.search_gst.assert_not_called()
        research.search_turnover.assert_called_once()

    @patch("services.gst_turnover_enrichment.service.settings.ENRICHMENT_LOOKUP_TURNOVER", False)
    def test_turnover_lookup_disabled_skips_ai_research(self):
        service, research = self._service(gst_result=_found_gst())

        service.resolve("Ambica Electro Control", None)

        research.search_turnover.assert_not_called()

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_agreeing_sources_from_different_domains_boost_confidence(self, _validate):
        # Two distinct domains citing the exact same GSTIN is genuine
        # multi-source corroboration - the existing confidence-scoring
        # bonus for that must still apply here, unchanged.
        multi_source = _found_gst(sources=["https://a.example/x", "https://b.example/y"])
        single_source = _found_gst(sources=["https://a.example/x"])

        service_multi, _ = self._service(gst_result=multi_source)
        service_single, _ = self._service(gst_result=single_source)

        result_multi = service_multi.resolve("Ambica Electro Control", None)
        result_single = service_single.resolve("Ambica Electro Control", None)

        self.assertGreater(result_multi.gst.confidence, result_single.gst.confidence)

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_gst_candidate_conflicting_with_companys_known_state_is_demoted(self, _validate):
        # "27" = Maharashtra, "33" = Tamil Nadu (GST state-code prefixes).
        # The Maharashtra candidate has a *higher* base score (a stronger
        # source tier) than the Tamil Nadu one, so without the state-conflict
        # demotion it would win outright despite belonging to a different
        # company's registration entirely - this is exactly the multi-GSTIN-
        # on-one-page scenario the evidence/state-check work is meant to catch.
        candidates = [
            SourceCandidate(
                value="27AAPFU0939F1ZV", source="website", source_url="https://directory.example/other",
                evidence="Supplier GSTIN: 27AAPFU0939F1ZV",
            ),
            SourceCandidate(
                value="33AAPFU0939F1Z2", source="search", source_url="https://directory.example/ambica",
                evidence="Ambica Electro Control GSTIN: 33AAPFU0939F1Z2",
            ),
        ]
        self.assertGreater(
            confidence.GST_SOURCE_POINTS["website"], confidence.GST_SOURCE_POINTS["search"],
        )

        result = GstTurnoverEnrichmentService._resolve_field(
            "GST Number", "Ambica Electro Control", candidates, confidence.GST_SOURCE_POINTS,
            state="Tamil Nadu",
        )

        self.assertEqual(result.value, "33AAPFU0939F1Z2")

    @patch("services.gst_turnover_enrichment.service.ai_validator.validate", return_value=None)
    def test_gst_candidate_with_no_state_conflict_is_unaffected(self, _validate):
        candidates = [
            SourceCandidate(
                value="27AAPFU0939F1ZV", source="website", source_url="https://directory.example/ambica",
                evidence="Ambica Electro Control GSTIN: 27AAPFU0939F1ZV",
            ),
        ]
        result = GstTurnoverEnrichmentService._resolve_field(
            "GST Number", "Ambica Electro Control", candidates, confidence.GST_SOURCE_POINTS,
            state="Maharashtra",
        )

        self.assertEqual(result.value, "27AAPFU0939F1ZV")


if __name__ == "__main__":
    unittest.main()
