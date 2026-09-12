import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse, parse_qs

from services.geocoding_service import GeoapifyGeocodingService


def _fake_response(body: dict):
    response = MagicMock()
    response.read.return_value = json.dumps(body).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


_INDIA_RESULT = {
    "results": [{
        "country_code": "in", "city": "Chennai", "state": "Tamil Nadu",
        "district": "Chennai", "suburb": "Guindy", "place_id": "abc",
        "lat": 13.08, "lon": 80.27,
    }]
}


class GeocodingServicePreferCityTests(unittest.TestCase):
    def setUp(self):
        self.service = GeoapifyGeocodingService(api_key="fake-key")

    @patch("services.geocoding_service.urlopen")
    def test_default_call_does_not_send_type_city(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(_INDIA_RESULT)

        self.service.geocode("42 Race Course Road, Coimbatore")

        requested_url = mock_urlopen.call_args.args[0].full_url
        query = parse_qs(urlparse(requested_url).query)
        self.assertNotIn("type", query)

    @patch("services.geocoding_service.urlopen")
    def test_prefer_city_sends_type_city(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(_INDIA_RESULT)

        self.service.geocode("Delhi", prefer_city=True)

        requested_url = mock_urlopen.call_args.args[0].full_url
        query = parse_qs(urlparse(requested_url).query)
        self.assertEqual(query.get("type"), ["city"])

    @patch("services.geocoding_service.urlopen")
    def test_prefer_city_and_default_are_cached_separately(self, mock_urlopen):
        # Same text, different mode - must not share a cache entry, since
        # Geoapify can legitimately return a different result for each
        # (see the module docstring: a bare city name vs. a full address).
        mock_urlopen.return_value = _fake_response(_INDIA_RESULT)

        self.service.geocode("Delhi", prefer_city=False)
        self.service.geocode("Delhi", prefer_city=True)

        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("services.geocoding_service.urlopen")
    def test_prefer_city_result_is_cached_on_repeat_call(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response(_INDIA_RESULT)

        self.service.geocode("Chennai", prefer_city=True)
        self.service.geocode("Chennai", prefer_city=True)

        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("services.geocoding_service.urlopen")
    def test_no_api_key_returns_none_without_a_request(self, mock_urlopen):
        # api_key="" is falsy, so GeoapifyGeocodingService.__init__ falls
        # back to settings.GEOAPIFY_API_KEY (real config) unless that's also
        # patched empty here - this isolates the "no key configured" case
        # without depending on whatever key happens to be in the real .env.
        with patch("services.geocoding_service.settings.GEOAPIFY_API_KEY", ""):
            service = GeoapifyGeocodingService()
            self.assertIsNone(service.geocode("Chennai", prefer_city=True))
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
