import unittest

from config.geography import CITY_LOCALITIES, classify_location, hierarchy_ids, location_query_variants


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

    def test_district_request_matches_a_city_with_that_seeded_parent(self):
        decision, _ = classify_location("Chennai", "Tamil Nadu", None, "Chennai district")
        self.assertEqual(decision, "match")

    def test_district_request_rejects_a_city_in_a_different_known_district(self):
        decision, _ = classify_location("Hosur", "Tamil Nadu", None, "Chennai")
        self.assertEqual(decision, "outside")

    def test_locality_request_matches_company_locality(self):
        decision, _ = classify_location(
            "Chennai", "Tamil Nadu", "Ambattur, Chennai, Tamil Nadu",
            "Ambattur", company_locality="Ambattur",
        )
        self.assertEqual(decision, "match")

    def test_city_only_company_is_unknown_for_a_more_specific_locality_request(self):
        decision, _ = classify_location("Chennai", "Tamil Nadu", None, "Ambattur")
        self.assertEqual(decision, "unknown")

    def test_confirmed_different_locality_is_outside(self):
        decision, _ = classify_location(
            "Chennai", "Tamil Nadu", None, "Ambattur", company_locality="Guindy",
        )
        self.assertEqual(decision, "outside")


class LocationQueryVariantsTests(unittest.TestCase):
    def test_unseeded_city_returns_only_the_original_location(self):
        self.assertEqual(location_query_variants("Dubai"), ["Dubai"])

    def test_empty_or_missing_location_is_returned_unchanged(self):
        self.assertEqual(location_query_variants(""), [""])
        self.assertEqual(location_query_variants(None), [None])

    def test_seeded_city_expands_to_itself_plus_each_known_locality(self):
        variants = location_query_variants("Coimbatore")
        self.assertEqual(variants[0], "Coimbatore")
        self.assertEqual(len(variants), 1 + len(CITY_LOCALITIES["Coimbatore"]))
        for locality in CITY_LOCALITIES["Coimbatore"]:
            self.assertIn(f"{locality}, Coimbatore", variants)

    def test_alias_resolves_to_the_canonical_city_before_expanding(self):
        variants = location_query_variants("Bangalore")
        self.assertIn("Peenya, Bengaluru", variants)


class HierarchyIdTests(unittest.TestCase):
    def test_ids_are_stable_for_a_resolved_hierarchy(self):
        ids = hierarchy_ids("Tamil Nadu", "Chennai", "Chennai", "Ambattur")
        self.assertEqual(ids["state_id"], "in.state.tamilnadu")
        self.assertEqual(ids["district_id"], "in.state.tamilnadu.district.chennai")
        self.assertEqual(ids["city_id"], "in.state.tamilnadu.district.chennai.city.chennai")
        self.assertTrue(ids["location_id"].endswith(".locality.ambattur"))


if __name__ == "__main__":
    unittest.main()
