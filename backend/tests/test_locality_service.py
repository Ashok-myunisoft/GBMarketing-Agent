import json
import unittest
from unittest.mock import MagicMock, patch

from services.locality_service import GeoapifyLocalityService


def _fake_response(payload: dict):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class GeoapifyLocalityServiceTests(unittest.TestCase):
    @patch("services.locality_service.settings")
    def test_not_configured_without_an_api_key_returns_no_localities(self, mock_settings):
        # Falls back to settings.GEOAPIFY_API_KEY whenever no explicit key is
        # passed in, so patch settings itself rather than passing a falsy
        # api_key - this environment's real .env may have a key configured,
        # which a falsy constructor arg would otherwise still fall back to.
        mock_settings.GEOAPIFY_API_KEY = None
        service = GeoapifyLocalityService()

        self.assertFalse(service.is_configured)
        self.assertEqual(service.localities_for("Chennai"), [])

    def test_blank_city_returns_no_localities_without_making_a_request(self):
        service = GeoapifyLocalityService(api_key="key")

        with patch("services.locality_service.urlopen") as urlopen:
            self.assertEqual(service.localities_for(""), [])
            urlopen.assert_not_called()

    @patch("services.locality_service.time.sleep", return_value=None)
    @patch("services.locality_service.urlopen")
    def test_resolves_place_id_then_fetches_and_dedupes_localities(self, urlopen, _sleep):
        geocode_payload = {"results": [{"country_code": "in", "place_id": "abc123"}]}
        places_payload = {
            "features": [
                {"properties": {"name": "Ambattur"}},
                {"properties": {"name": "Guindy"}},
                {"properties": {"name": "Ambattur"}},  # duplicate, should be dropped
                {"properties": {}},  # no name, should be skipped
            ]
        }
        urlopen.side_effect = [_fake_response(geocode_payload), _fake_response(places_payload)]

        service = GeoapifyLocalityService(api_key="key")
        result = service.localities_for("Chennai")

        self.assertEqual(result, ["Ambattur", "Guindy"])
        self.assertEqual(urlopen.call_count, 2)

    @patch("services.locality_service.time.sleep", return_value=None)
    @patch("services.locality_service.urlopen")
    def test_second_call_for_the_same_city_is_served_from_cache(self, urlopen, _sleep):
        geocode_payload = {"results": [{"country_code": "in", "place_id": "abc123"}]}
        places_payload = {"features": [{"properties": {"name": "Ambattur"}}]}
        urlopen.side_effect = [_fake_response(geocode_payload), _fake_response(places_payload)]

        service = GeoapifyLocalityService(api_key="key")
        first = service.localities_for("Chennai")
        second = service.localities_for("Chennai")

        self.assertEqual(first, second)
        self.assertEqual(urlopen.call_count, 2)  # not 4 - second call hit the cache

    @patch("services.locality_service.time.sleep", return_value=None)
    @patch("services.locality_service.urlopen")
    def test_a_country_mismatch_resolves_to_no_localities(self, urlopen, _sleep):
        urlopen.return_value = _fake_response(
            {"results": [{"country_code": "ae", "place_id": "xyz"}]}
        )

        service = GeoapifyLocalityService(api_key="key")

        self.assertEqual(service.localities_for("Dubai"), [])
        self.assertEqual(urlopen.call_count, 1)  # never reaches the Places call

    @patch("services.locality_service.time.sleep", return_value=None)
    @patch("services.locality_service.urlopen")
    def test_a_network_failure_returns_no_localities_instead_of_raising(self, urlopen, _sleep):
        urlopen.side_effect = RuntimeError("boom")

        service = GeoapifyLocalityService(api_key="key")

        self.assertEqual(service.localities_for("Chennai"), [])


if __name__ == "__main__":
    unittest.main()
