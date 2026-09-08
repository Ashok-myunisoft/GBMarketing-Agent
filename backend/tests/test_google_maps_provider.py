import unittest

from providers.google_maps_provider import GoogleMapsProvider
from schemas.company import Company


def _company(address=None, city=None, state=None):
    return Company(company_name="Acme", address=address, city=city, state=state)


class GoogleMapsLocationFilterTests(unittest.TestCase):
    def test_no_requested_location_keeps_everything(self):
        self.assertTrue(
            GoogleMapsProvider._is_within_requested_location(_company(address="Chennai"), None)
        )

    def test_result_naming_a_different_known_city_is_dropped(self):
        company = _company(address="12 GST Road, Chennai, Tamil Nadu")
        self.assertFalse(
            GoogleMapsProvider._is_within_requested_location(company, "Coimbatore")
        )

    def test_result_naming_a_different_known_locality_is_dropped(self):
        # No city spelled out at all - only a locality unambiguously in Chennai.
        company = _company(address="14 Anna Nagar West, Ambattur")
        self.assertFalse(
            GoogleMapsProvider._is_within_requested_location(company, "Coimbatore")
        )

    def test_result_naming_the_requested_city_is_kept(self):
        company = _company(address="42 Race Course Road, Coimbatore")
        self.assertTrue(
            GoogleMapsProvider._is_within_requested_location(company, "Coimbatore")
        )

    def test_result_with_no_resolvable_location_is_kept_as_unverified(self):
        # Genuinely ambiguous - no known city/locality in the address at all.
        # ValidationAgent, not the provider, is responsible for flagging this.
        company = _company(address="Plot 4, Industrial Estate")
        self.assertTrue(
            GoogleMapsProvider._is_within_requested_location(company, "Coimbatore")
        )


if __name__ == "__main__":
    unittest.main()
