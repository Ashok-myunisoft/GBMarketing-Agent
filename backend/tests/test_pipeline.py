import unittest
from unittest.mock import patch

from services.contact_extraction.models import ContactCandidate, PageCategory
from services.contact_extraction.pipeline import ContactExtractionPipeline


class PipelineSelectBestTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = ContactExtractionPipeline("ABC Pvt Ltd", "https://abc.example.com")

    def test_returns_none_for_no_candidates(self):
        self.assertIsNone(self.pipeline.select_best([]))

    def test_picks_the_highest_scoring_candidate(self):
        leadership_ceo = ContactCandidate(
            name="Ravi Kumar",
            raw_title="CEO",
            canonical_designation="CEO",
            page_category=PageCategory.LEADERSHIP,
            source_url="https://abc.example.com/leadership",
        )
        contact_manager = ContactCandidate(
            name="Someone Else",
            raw_title="Manager",
            canonical_designation="Manager",
            page_category=PageCategory.CONTACT,
            source_url="https://abc.example.com/contact",
        )

        result = self.pipeline.select_best([contact_manager, leadership_ceo])

        self.assertEqual(result.contact_person, "Ravi Kumar")
        self.assertEqual(result.designation, "CEO")

    def test_drops_a_low_confidence_lone_candidate(self):
        weak = ContactCandidate(
            name="Someone Else",
            raw_title="Manager",
            canonical_designation="Manager",
            page_category=PageCategory.OTHER,
            source_url="https://abc.example.com/contact",
        )
        with patch("services.contact_extraction.pipeline.settings.ENRICHMENT_CONTACT_MIN_CONFIDENCE", 1000):
            self.assertIsNone(self.pipeline.select_best([weak]))

    def test_external_candidate_rejects_non_name_input(self):
        self.assertIsNone(self.pipeline.external_candidate("Products Team", "Manager", "filesure"))

    def test_external_candidate_wraps_a_valid_hit(self):
        candidate = self.pipeline.external_candidate("Ravi Kumar", "MD", "filesure", source_url="filesure:U1")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.name, "Ravi Kumar")
        self.assertEqual(candidate.canonical_designation, "Managing Director")
        self.assertEqual(candidate.source, "filesure")

    def test_cross_source_agreement_beats_a_lone_website_hit(self):
        website_only = [
            ContactCandidate(
                name="Ravi Kumar",
                raw_title="Director",
                canonical_designation="Director",
                page_category=PageCategory.CONTACT,
                source_url="https://abc.example.com/contact",
                source="website",
            )
        ]
        confirmed = website_only + [
            self.pipeline.external_candidate("Ravi Kumar", "Director", "filesure", source_url="filesure:U1")
        ]

        lone_result = self.pipeline.select_best(website_only)
        confirmed_result = self.pipeline.select_best(confirmed)

        self.assertGreater(confirmed_result.confidence, lone_result.confidence)

    def test_llm_disambiguator_is_not_consulted_by_default(self):
        # ENRICHMENT_LOOKUP_LLM_CONTACT defaults to False, so a tie between
        # two equally-scored candidates should resolve deterministically
        # (first after the stable sort) without ever touching the LLM.
        with patch("services.contact_extraction.pipeline.llm_disambiguator.disambiguate") as mock_disambiguate:
            tied_a = ContactCandidate(
                name="Ravi Kumar", raw_title="Director", canonical_designation="Director",
                page_category=PageCategory.CONTACT, source_url="https://abc.example.com/a",
            )
            tied_b = ContactCandidate(
                name="Someone Else", raw_title="Director", canonical_designation="Director",
                page_category=PageCategory.CONTACT, source_url="https://abc.example.com/b",
            )
            self.pipeline.select_best([tied_a, tied_b])
            mock_disambiguate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
