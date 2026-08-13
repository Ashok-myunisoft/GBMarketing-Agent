import unittest

from config.geography import classify_location


class ClassifyLocationTests(unittest.TestCase):
    def test_state_request_matches_any_city_within_that_state(self):
        for city in ("Ahmedabad", "Gandhinagar", "Vadodara"):
            decision, _ = classify_location(city, "Gujarat", f"{city}, Gujarat, India", "Gujarat")
            self.assertEqual(decision, "match", city)

    def test_state_request_rejects_a_company_confirmed_in_a_different_state(self):
        decision, reason = classify_location("Pune", "Maharashtra", "Pune, Maharashtra, India", "Gujarat")
        self.assertEqual(decision, "outside")
        self.assertIn("Maharashtra", reason)
        self.assertIn("Gujarat", reason)

    def test_state_request_with_no_company_state_data_is_unknown_not_outside(self):
        decision, _ = classify_location(None, None, None, "Gujarat")
        self.assertEqual(decision, "unknown")

    def test_state_recovered_from_address_when_state_field_is_blank(self):
        decision, _ = classify_location("Ahmedabad", None, "Ahmedabad, Gujarat, India", "Gujarat")
        self.assertEqual(decision, "match")

    def test_city_request_matches_the_same_city(self):
        decision, _ = classify_location("Ahmedabad", "Gujarat", "Ahmedabad, Gujarat, India", "Ahmedabad")
        self.assertEqual(decision, "match")

    def test_city_request_rejects_a_different_structured_city(self):
        decision, reason = classify_location("Gandhinagar", "Gujarat", "Gandhinagar, Gujarat, India", "Ahmedabad")
        self.assertEqual(decision, "outside")
        self.assertIn("Gandhinagar", reason)

    def test_city_request_with_no_structured_city_falls_back_to_address_substring(self):
        decision, _ = classify_location(None, None, "Plot 4, Ahmedabad, Gujarat", "Ahmedabad")
        self.assertEqual(decision, "match")

    def test_city_request_with_no_city_or_matching_address_is_unknown_not_outside(self):
        decision, _ = classify_location(None, None, None, "Ahmedabad")
        self.assertEqual(decision, "unknown")

    def test_known_city_aliases_still_resolve(self):
        decision, _ = classify_location("Bengaluru", "Karnataka", None, "Bangalore")
        self.assertEqual(decision, "match")

    def test_unrecognised_requested_string_with_no_company_location_data_is_unknown(self):
        # e.g. a country/region this codebase has no reference data for -
        # never confidently "outside" without real evidence.
        decision, _ = classify_location(None, None, None, "Dubai")
        self.assertEqual(decision, "unknown")

    def test_structured_city_confidently_differs_from_an_unrecognised_requested_string(self):
        # company_city is a real structured field, so a clear mismatch is
        # positive evidence even when the requested string isn't one of our
        # known aliases/states.
        decision, _ = classify_location("Ahmedabad", "Gujarat", None, "Dubai")
        self.assertEqual(decision, "outside")

    def test_empty_requested_location_always_matches(self):
        decision, _ = classify_location(None, None, None, "")
        self.assertEqual(decision, "match")

    def test_city_request_tolerates_a_typo_of_the_same_city(self):
        decision, _ = classify_location("Ahmedabad", "Gujarat", "Ahmedabad, Gujarat, India", "Ahamedabad")
        self.assertEqual(decision, "match")

    def test_city_request_typo_still_rejects_a_genuinely_different_city(self):
        decision, _ = classify_location("Gandhinagar", "Gujarat", "Gandhinagar, Gujarat, India", "Ahamedabad")
        self.assertEqual(decision, "outside")

    def test_state_request_tolerates_a_typo_of_the_same_state(self):
        decision, _ = classify_location("Ahmedabad", "Gujarat", "Ahmedabad, Gujarat, India", "Gujrat")
        self.assertEqual(decision, "match")


if __name__ == "__main__":
    unittest.main()
