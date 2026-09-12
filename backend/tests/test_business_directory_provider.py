import unittest
from unittest.mock import MagicMock

from providers.business_directory_provider import BusinessDirectoryProvider
from schemas.company import Company
from services.geocoding_service import GeocodedAddress


def _company(city=None):
    return Company(company_name="Acme", city=city)


def _geocoder(city=None, state=None):
    """A fake GeoapifyGeocodingService - no real network call - returning a
    fixed city/state for any input text, so tests can pin the exact
    behavior without depending on live Geoapify data. Never returns
    ``district`` - verified against the real API that Geoapify's district
    granularity doesn't match this codebase's city/locality model (e.g. it
    resolves "Ambattur" to its own district, not "Chennai"), so the provider
    never reads that field at all - only city/state."""
    geocoder = MagicMock()
    geocoder.geocode.return_value = GeocodedAddress(city=city, state=state)
    return geocoder


class TradeIndiaLocationFilterTests(unittest.TestCase):
    def test_no_requested_location_keeps_everything(self):
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(_company(city="Chennai"), None)
        )

    def test_result_naming_a_different_known_city_is_dropped_without_a_geocoder(self):
        # No geocoder supplied - falls back to the seed CITY_ALIASES/
        # CITY_DISTRICTS list only (same pair test_google_maps_provider.py
        # uses). A city with no seed data and no state-name overlap (e.g.
        # "Rajkot") would stay "unknown" here - see the geocoder-backed
        # tests below for how that gets resolved.
        company = _company(city="Coimbatore")
        self.assertFalse(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai")
        )

    def test_a_state_name_city_is_dropped_even_without_a_geocoder(self):
        # "Delhi" is itself a recognised STATE_NAMES entry - the raw city
        # text is also passed through as company_address, so
        # classify_location's own free-text state-name matching catches
        # this conflict even with no geocoder at all (a bonus of feeding the
        # raw text through, not something requiring real geocoded data).
        company = _company(city="Delhi")
        self.assertFalse(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai")
        )

    def test_result_naming_the_requested_city_is_kept(self):
        company = _company(city="Chennai")
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai")
        )

    def test_result_with_no_city_at_all_is_kept_as_unverified(self):
        # TradeIndia's list view sometimes has no parseable city text -
        # ValidationAgent, not the provider, is responsible for flagging this.
        company = _company(city=None)
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai")
        )

    def test_unseeded_city_is_dropped_via_geocoded_state_conflict(self):
        # "Delhi" isn't in the seed lists at all, so without real geocoded
        # data classify_location has nothing to compare and stays "unknown".
        # Real Geoapify data (verified by hand) returns no `state` for a
        # bare "Delhi" city-type match - the state-level conflict is instead
        # caught via classify_location's own free-text state-name matching
        # against the raw city text passed as company_address, which is why
        # this mock deliberately supplies no state either.
        company = _company(city="Delhi")
        geocoder = _geocoder(city="New Delhi", state=None)
        self.assertFalse(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai", geocoder=geocoder)
        )
        geocoder.geocode.assert_called_once_with("Delhi", prefer_city=True)

    def test_unseeded_city_is_dropped_when_geocoder_resolves_a_conflicting_state(self):
        # Real Geoapify data for "Rajkot"/"Gurgaon" (verified by hand) does
        # return a state, letting this resolve as a plain state conflict.
        company = _company(city="Rajkot")
        geocoder = _geocoder(city="Rajkot", state="Gujarat")
        self.assertFalse(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai", geocoder=geocoder)
        )

    def test_locality_of_the_requested_city_is_kept_not_dropped(self):
        # "Ambattur" is a real Chennai locality (see CITY_LOCALITIES) -
        # Geoapify resolves its parent *city* correctly to "Chennai" even
        # though its own *district* field would say "Ambattur" (verified by
        # hand) - this is exactly why the provider must never read the
        # geocoded district, only city/state.
        company = _company(city="Ambattur")
        geocoder = _geocoder(city="Chennai", state="Tamil Nadu")
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai", geocoder=geocoder)
        )

    def test_unseeded_city_is_kept_when_geocoder_confirms_the_requested_city(self):
        company = _company(city="Chennai")
        geocoder = _geocoder(city="Chennai", state="Tamil Nadu")
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai", geocoder=geocoder)
        )

    def test_geocoder_failure_stays_unknown_not_dropped(self):
        # A geocoding failure (returns None) must degrade to the existing
        # seed-list-only/free-text behavior, never crash or wrongly reject.
        # "Rajkot" (unlike "Delhi") isn't itself a recognised state name, so
        # with no working geocoder there's genuinely no evidence either way.
        company = _company(city="Rajkot")
        geocoder = MagicMock()
        geocoder.geocode.return_value = None
        self.assertTrue(
            BusinessDirectoryProvider._is_within_requested_location(company, "Chennai", geocoder=geocoder)
        )


if __name__ == "__main__":
    unittest.main()
