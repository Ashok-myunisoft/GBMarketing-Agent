import unittest

from services.contact_extraction import scorer
from services.contact_extraction.models import ContactCandidate, PageCategory


def _candidate(**overrides):
    defaults = dict(
        name="Ravi Kumar",
        raw_title="Managing Director",
        canonical_designation="Managing Director",
        page_category=PageCategory.CONTACT,
        source_url="https://example.com/contact",
    )
    defaults.update(overrides)
    return ContactCandidate(**defaults)


class ScorerTests(unittest.TestCase):
    def test_leadership_ceo_outranks_contact_page_manager(self):
        ceo = _candidate(
            canonical_designation="CEO", raw_title="CEO", page_category=PageCategory.LEADERSHIP
        )
        manager = _candidate(
            name="Someone Else", canonical_designation="Manager", raw_title="Manager",
            page_category=PageCategory.CONTACT,
        )
        self.assertGreater(scorer.score(ceo), scorer.score(manager))

    def test_cross_source_agreement_outranks_single_source(self):
        lone = _candidate()
        lone.matched_sources = {"website"}

        confirmed = _candidate()
        confirmed.matched_sources = {"website", "filesure"}

        self.assertGreater(scorer.score(confirmed), scorer.score(lone))

    def test_linkedin_url_adds_a_bonus(self):
        without_linkedin = _candidate()
        with_linkedin = _candidate(linkedin_url="https://linkedin.com/in/ravikumar")
        self.assertGreater(scorer.score(with_linkedin), scorer.score(without_linkedin))

    def test_corporate_email_outscores_unrelated_email(self):
        corporate = _candidate(email="ravi@example.com")
        personal = _candidate(email="ravi@gmail.com")
        self.assertGreater(
            scorer.score(corporate, company_website="https://example.com"),
            scorer.score(personal, company_website="https://example.com"),
        )

    def test_unknown_designation_falls_back_to_default_weight(self):
        candidate = _candidate(canonical_designation="Something Unusual", raw_title="Something Unusual")
        self.assertEqual(scorer.designation_weight("Something Unusual"), scorer.DEFAULT_DESIGNATION_WEIGHT)


if __name__ == "__main__":
    unittest.main()
