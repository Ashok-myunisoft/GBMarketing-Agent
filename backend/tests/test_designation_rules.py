import unittest

from services.contact_extraction.designation_rules import canonical_designation


class DesignationRulesTests(unittest.TestCase):
    def test_managing_director_synonyms_reconcile(self):
        for title in ("MD", "Managing Director", "Founder & Managing Director"):
            self.assertEqual(canonical_designation(title), "Managing Director")

    def test_ceo_synonyms_reconcile(self):
        for title in ("CEO", "Chief Executive Officer", "Founder & CEO"):
            self.assertEqual(canonical_designation(title), "CEO")

    def test_director_synonyms_reconcile(self):
        self.assertEqual(canonical_designation("Director"), "Director")
        self.assertEqual(canonical_designation("Executive Director"), "Executive Director")
        self.assertEqual(canonical_designation("Whole Time Director"), "Whole-time Director")

    def test_distinct_senior_designations_are_preserved(self):
        self.assertEqual(canonical_designation("Managing Partner"), "Managing Partner")

    def test_functional_head_synonyms_reconcile(self):
        self.assertEqual(canonical_designation("Purchase Head"), "Procurement Head")
        self.assertEqual(canonical_designation("Procurement Head"), "Procurement Head")
        self.assertEqual(canonical_designation("Head Supply Chain"), "Supply Chain")
        self.assertEqual(canonical_designation("Commercial Manager"), "Commercial Head")
        self.assertEqual(canonical_designation("Materials Manager"), "Stores Manager")
        self.assertEqual(canonical_designation("People Operations"), "HR Head")

    def test_abbreviations_reconcile(self):
        self.assertEqual(canonical_designation("VP"), "Vice President")
        self.assertEqual(canonical_designation("GM"), "General Manager")

    def test_free_text_falls_back_to_substring_match(self):
        self.assertEqual(
            canonical_designation("Founder, Managing Director, ABC Pvt Ltd"),
            "Managing Director",
        )

    def test_unrelated_text_returns_none(self):
        self.assertIsNone(canonical_designation("Our Products"))
        self.assertIsNone(canonical_designation(""))
        self.assertIsNone(canonical_designation(None))

    def test_marketing_sentence_containing_a_taxonomy_phrase_is_rejected(self):
        # Regression: seen live on redbridgevalves.com - a "Delivery
        # Reliable" feature card's body text happens to contain "supply
        # chain" as a substring, but it's marketing prose, not a title.
        self.assertIsNone(
            canonical_designation(
                "Our supply chain efficiency and optimum turnaround time "
                "make our deliveries reliable"
            )
        )


if __name__ == "__main__":
    unittest.main()
