"""Dynamic suburb/neighbourhood discovery through Geoapify.

config/geography.CITY_LOCALITIES is a hand-picked seed list covering only
a handful of cities and a few localities each - useful, but not close to
comprehensive (see its own docstring: "a starting seed, not exhaustive").
This service supplements that seed with Geoapify's own Places API so
search fan-out (services/search_services.py) can reach real coverage for
whatever city was requested, seeded or not, instead of only ever knowing
about the localities someone happened to add to the static list.

Two Geoapify calls are needed: first geocode the city to get its
`place_id` (a boundary identifier), then query the Places API for
`populated_place.suburb`/`neighbourhood`/`district` features filtered to
that boundary. Both calls share the geocoding service's existing
5-requests/second throttle margin.
"""

import json
import logging
import time
from typing import List, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.config import settings

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
PLACES_URL = "https://api.geoapify.com/v2/places"
LOCALITY_CATEGORIES = "populated_place.suburb,populated_place.neighbourhood,populated_place.district"
# Discovery retrieves pages until the provider has no more results. Search
# fan-out applies its own lower cap, so complete discovery data can later be
# reused by hierarchy resolution without generating unlimited provider calls.
LOCALITY_PAGE_SIZE = 100


class GeoapifyLocalityService:
    """Rate-limited, cached suburb/neighbourhood lookup for a city name.

    Every method degrades to an empty list rather than raising - callers
    are expected to merge this with (never replace) the static seed, so
    any failure here (missing key, unresolvable city, network error)
    just means no *extra* coverage that run, never less than before.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.GEOAPIFY_API_KEY
        self._cache: "dict[tuple[str, Optional[int]], List[str]]" = {}
        self._last_request_at = 0.0

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def localities_for(self, city: str, max_localities: Optional[int] = None) -> List[str]:
        """Returns known suburbs/neighbourhoods/districts for ``city``.

        ``max_localities`` stops discovery as soon as enough entries have
        been read, avoiding pages that SearchService would not use.
        """

        if not self._api_key or not city or not city.strip():
            return []

        cache_key = (city.strip().lower(), max_localities)

        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            place_id = self._resolve_place_id(city)

            if not place_id:
                localities: List[str] = []
            else:
                localities = self._fetch_localities(place_id, max_localities=max_localities)

        except Exception as exc:
            logger.warning("Geoapify locality discovery failed for '%s': %s", city, exc)
            localities = []

        self._cache[cache_key] = localities

        return localities

    def _throttle(self) -> None:
        # Geoapify's free plan permits five requests/second; keep a margin
        # (same figure services/geocoding_service.py uses).
        wait = 0.22 - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)

    def _get(self, url: str) -> dict:
        self._throttle()
        request = Request(url, headers={"User-Agent": "MarketingAgent/1.0"})
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._last_request_at = time.monotonic()
        return payload

    def _resolve_place_id(self, city: str) -> Optional[str]:
        query = urlencode({
            "text": city,
            "filter": "countrycode:in",
            "type": "city",
            "format": "json",
            "limit": 1,
            "apiKey": self._api_key,
        })
        payload = self._get(f"{GEOCODE_URL}?{query}")
        result = (payload.get("results") or [None])[0]

        if not result or result.get("country_code", "").lower() != "in":
            return None

        return result.get("place_id")

    def _fetch_localities(self, place_id: str, max_localities: Optional[int] = None) -> List[str]:
        names: List[str] = []
        seen = set()
        offset = 0

        while True:
            query = urlencode({
                "categories": LOCALITY_CATEGORIES,
                "filter": f"place:{place_id}",
                "limit": min(LOCALITY_PAGE_SIZE, max_localities) if max_localities else LOCALITY_PAGE_SIZE,
                "offset": offset,
                "apiKey": self._api_key,
            })
            payload = self._get(f"{PLACES_URL}?{query}")
            features = payload.get("features", [])

            for feature in features:
                name = (feature.get("properties") or {}).get("name")

                if not name:
                    continue

                key = name.strip().lower()

                if key and key not in seen:
                    seen.add(key)
                    names.append(name)
                    if max_localities and len(names) >= max_localities:
                        return names

            page_size = min(LOCALITY_PAGE_SIZE, max_localities) if max_localities else LOCALITY_PAGE_SIZE
            if len(features) < page_size:
                break
            offset += page_size

        return names
