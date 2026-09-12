import unittest

from config.geography import (
    CITY_LOCALITIES,
    CITY_STATE,
    canonical_city,
    city_for_locality,
    classify_location,
    gst_state_conflict,
    hierarchy_ids,
    location_query_variants,
)


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

    def test_locality_request_for_a_city_whose_district_name_differs_catches_a_different_named_city(self):
        # Bengaluru's district is "Bengaluru Urban" (not "Bengaluru"), so a
        # locality-suffixed request like "Peenya, Bengaluru" never reaches
        # the district branch's own address-fallback at all - this exercises
        # the locality branch's matching fallback instead. Without it, a
        # Chennai company surfaced by a Bengaluru locality search (Google
        # Maps padding a sparse local result set) would only ever come back
        # "unknown", never "outside".
        decision, reason = classify_location(
            None, None, "45 Anna Salai, Chennai, Tamil Nadu", "Peenya, Bengaluru",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)

    def test_locality_request_for_hosur_catches_a_different_named_city(self):
        # Hosur's district is "Krishnagiri", the same city/district-name
        # mismatch as Bengaluru.
        decision, reason = classify_location(
            None, None, "Plot 9, SIDCO Estate, Chennai", "Hosur SIPCOT, Hosur",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)

    def test_locality_request_still_matches_the_genuine_locality(self):
        decision, _ = classify_location(
            None, None, "Plot 4, Peenya Industrial Area, Bengaluru", "Peenya, Bengaluru",
        )
        self.assertEqual(decision, "match")

    def test_locality_request_with_no_city_evidence_at_all_stays_unknown(self):
        decision, _ = classify_location(None, None, "Plot 4, Industrial Estate", "Peenya, Bengaluru")
        self.assertEqual(decision, "unknown")

    def test_city_request_catches_a_company_confidently_in_a_different_state(self):
        # Delhi/Mumbai aren't in CITY_ALIASES/CITY_LOCALITIES at all - the
        # small seeded-city checks above can never recognise them by name.
        # STATE_NAMES covers them though, so this is the broader signal that
        # closes that gap: a Delhi or Mumbai listing surfaced by a loosely
        # geo-scoped Coimbatore search now gets caught by state, not just by
        # the tiny South-Indian city/locality seed lists.
        decision, reason = classify_location(
            None, None, "123 Connaught Place, New Delhi, Delhi", "Coimbatore",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Delhi", reason)

    def test_locality_request_also_catches_a_company_in_a_different_state(self):
        # Kolkata is deliberately NOT a seeded city, so this exercises the
        # state-only fallback rather than the (now stronger, since Mumbai is
        # seeded) city-level check just above it.
        decision, reason = classify_location(
            None, None, "45 Park Street, Kolkata, West Bengal", "Peenya, Bengaluru",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("West Bengal", reason)

    def test_locality_request_catches_a_now_seeded_city_before_the_state_check(self):
        # Mumbai is now seeded (added from real query history), so this
        # mismatch is caught at the city level - a strictly more precise
        # result than the state-only fallback above.
        decision, reason = classify_location(
            None, None, "45 MG Road, Mumbai, Maharashtra", "Peenya, Bengaluru",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Mumbai", reason)

    def test_district_request_also_catches_a_company_in_a_different_state(self):
        decision, reason = classify_location(
            None, None, "Plot 9, SIDCO Estate, Kolkata, West Bengal", "Coimbatore district",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("West Bengal", reason)

    def test_newly_seeded_cities_from_real_query_history_are_recognised(self):
        # These were already being searched for (backend/data/jobs.sqlite3)
        # with zero seed data protecting them - added from real usage, not
        # speculatively.
        for city in ("Hyderabad", "Mumbai", "Pune", "Ahmedabad", "Kochi", "Sricity", "Kancheepuram", "Sivakasi"):
            self.assertEqual(canonical_city(city), city, city)
            self.assertIn(city, CITY_STATE, city)

    def test_a_different_newly_seeded_city_is_caught_for_a_newly_seeded_requested_city(self):
        decision, reason = classify_location(None, None, "Plot 2, MIDC, Pune, Maharashtra", "Hyderabad")
        self.assertEqual(decision, "outside")
        self.assertIn("Pune", reason)

    def test_common_misspelling_of_a_newly_seeded_city_still_resolves(self):
        # "Hydrabad" (missing an 'e') appears verbatim in real query history.
        decision, _ = classify_location("Hyderabad", "Telangana", None, "Hydrabad")
        self.assertEqual(decision, "match")

    def test_ahmedabad_alias_addition_does_not_break_existing_address_substring_fallback(self):
        # Regression guard for the exact case the normalization fix (making
        # requested_canonical always lowercase, never a mixed-case canonical
        # string) protects: this passed before Ahmedabad was seeded and must
        # keep passing now that it is.
        decision, _ = classify_location(None, None, "Plot 4, Ahmedabad, Gujarat", "Ahmedabad")
        self.assertEqual(decision, "match")

    def test_state_check_never_misfires_when_neither_side_is_resolvable(self):
        # No state evidence anywhere - must stay unknown, not outside.
        decision, _ = classify_location(None, None, "Plot 4, Industrial Estate", "Coimbatore")
        self.assertEqual(decision, "unknown")

    def test_unstructured_address_naming_a_different_known_city_is_outside(self):
        # A Chennai listing surfaced by a loosely geo-scoped Coimbatore
        # search, with no structured city/state resolved at all - the
        # address itself is the only signal, and it names a different
        # known city.
        decision, reason = classify_location(
            None, None, "12 GST Road, Chennai, Tamil Nadu", "Coimbatore",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)

    def test_unstructured_address_naming_a_different_known_locality_is_outside(self):
        # Same leak, but the address never spells out "Chennai" at all -
        # only a locality that is unambiguously a Chennai area. Coimbatore
        # doubles as both a city and a district name in the seed data, so
        # this is actually caught by the district branch (its resolved
        # district is Ambattur's parent city, Chennai) rather than the
        # plain-city fallback - either way, the leak is closed.
        decision, reason = classify_location(
            None, None, "14 Anna Nagar West, Ambattur", "Coimbatore",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)

    def test_unstructured_address_with_no_known_city_or_locality_stays_unknown(self):
        decision, _ = classify_location(None, None, "Plot 4, Industrial Estate", "Coimbatore")
        self.assertEqual(decision, "unknown")

    def test_unstructured_address_leak_caught_via_plain_city_branch_too(self):
        # "Bengaluru" (unlike "Coimbatore"/"Chennai") isn't itself a seeded
        # district name, so this exercises the plain-city fallback rather
        # than the district branch - same leak, same fix, different code path.
        decision, reason = classify_location(
            None, None, "12 GST Road, Chennai, Tamil Nadu", "Bengaluru",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)


class GstStateConflictTests(unittest.TestCase):
    def test_matching_state_is_not_a_conflict(self):
        self.assertIsNone(gst_state_conflict("27AAPFU0939F1ZV", "Maharashtra"))

    def test_different_state_is_a_conflict(self):
        # "27" is Maharashtra's GST state-code prefix.
        self.assertEqual(gst_state_conflict("27AAPFU0939F1ZV", "Tamil Nadu"), "Maharashtra")

    def test_state_recovered_from_address_when_state_field_is_blank(self):
        self.assertEqual(
            gst_state_conflict("27AAPFU0939F1ZV", None, "Some Street, Tamil Nadu"), "Maharashtra",
        )

    def test_unknown_company_state_is_not_a_conflict(self):
        self.assertIsNone(gst_state_conflict("27AAPFU0939F1ZV", None, None))

    def test_missing_or_too_short_gstin_is_not_a_conflict(self):
        self.assertIsNone(gst_state_conflict(None, "Tamil Nadu"))
        self.assertIsNone(gst_state_conflict("2", "Tamil Nadu"))

    def test_unrecognised_state_code_is_not_a_conflict(self):
        self.assertIsNone(gst_state_conflict("99AAPFU0939F1ZV", "Tamil Nadu"))


class CityForLocalityTests(unittest.TestCase):
    def test_known_locality_resolves_to_its_seeded_parent_city(self):
        self.assertEqual(city_for_locality("Ambattur"), "Chennai")
        self.assertEqual(city_for_locality("Peelamedu"), "Coimbatore")

    def test_unknown_locality_resolves_to_none(self):
        self.assertIsNone(city_for_locality("Nowhereville"))
        self.assertIsNone(city_for_locality(None))


class ClassifyLocationGeocodedOverrideTests(unittest.TestCase):
    """Covers requested_geocoded_* - the caller's own one-time geocoding of
    the *requested* location (e.g. via GeoapifyGeocodingService), used to
    make classify_location work at full district/city precision for a
    requested city outside the small hand-picked seed lists (e.g. "Nagpur",
    never seeded in CITY_ALIASES/CITY_DISTRICTS)."""

    def test_unseeded_requested_city_same_state_different_district_is_outside(self):
        # Company already resolved to Pune (e.g. by EnrichmentAgent's own
        # geocoding) - Nagpur itself is never seeded anywhere in this module.
        decision, reason = classify_location(
            "Pune", "Maharashtra", "Plot 4, MIDC, Pune, Maharashtra", "Nagpur",
            company_district="Pune",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Pune", reason)

    def test_unseeded_requested_city_genuine_match_is_match(self):
        decision, reason = classify_location(
            "Nagpur", "Maharashtra", "Plot 4, MIDC, Nagpur, Maharashtra", "Nagpur",
            company_district="Nagpur",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(decision, "match")

    def test_unseeded_requested_city_cross_state_leak_still_caught(self):
        decision, reason = classify_location(
            None, None, "123 Connaught Place, New Delhi, Delhi", "Nagpur",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Delhi", reason)

    def test_pre_enrichment_search_time_address_names_a_different_seeded_city(self):
        # No structured company fields at all (GoogleMapsProvider's
        # search-time filter, before EnrichmentAgent ever runs) - only the
        # raw address text, which names a city (Pune) that's seeded in
        # CITY_ALIASES but was never given a CITY_DISTRICTS entry.
        decision, reason = classify_location(
            None, None, "Plot 4, MIDC, Pune, Maharashtra", "Nagpur",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Pune", reason)

    def test_pre_enrichment_search_time_genuine_address_stays_unknown_not_outside(self):
        # Nagpur itself isn't seeded, so this can't be confirmed "match" pre-
        # enrichment - it must never be wrongly rejected either.
        decision, _ = classify_location(
            None, None, "Plot 4, MIDC, Nagpur, Maharashtra", "Nagpur",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(_, "company district could not be determined")

    def test_omitting_the_overrides_keeps_seed_list_only_behavior(self):
        # No requested_geocoded_* passed at all - must behave identically to
        # every pre-existing test in this file.
        decision, reason = classify_location(
            None, None, "12 GST Road, Chennai, Tamil Nadu", "Coimbatore",
        )
        self.assertEqual(decision, "outside")
        self.assertIn("Chennai", reason)

    def test_no_evidence_anywhere_stays_unknown_even_with_overrides_given(self):
        decision, _ = classify_location(
            None, None, "Plot 4, Industrial Estate", "Nagpur",
            requested_geocoded_state="Maharashtra", requested_geocoded_district="Nagpur",
            requested_geocoded_city="Nagpur",
        )
        self.assertEqual(decision, "unknown")


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
