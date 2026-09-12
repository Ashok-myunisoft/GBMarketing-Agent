"""Structured Indian address lookup through Geoapify."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.config import settings

logger = logging.getLogger(__name__)
GEOAPIFY_URL = "https://api.geoapify.com/v1/geocode/search"


@dataclass(frozen=True)
class GeocodedAddress:
    city: Optional[str]
    state: Optional[str]
    district: Optional[str] = None
    locality: Optional[str] = None
    location_id: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class GeoapifyGeocodingService:
    """Rate-limited, cached geocoding for verified company addresses."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.GEOAPIFY_API_KEY
        self._cache: dict[str, Optional[GeocodedAddress]] = {}
        self._last_request_at = 0.0

    def geocode(self, address: Optional[str], prefer_city: bool = False) -> Optional[GeocodedAddress]:
        """``prefer_city=True`` restricts Geoapify to administrative-place
        matches only (``type=city``) - required for a bare place name (e.g.
        "Delhi"), which otherwise free-text-matches any similarly-named
        business/POI (verified against the real API: "Delhi" alone resolves
        to a Coimbatore restaurant called "Delhi Sweet" without this,
        silently returning the wrong city/state/district entirely). Leave
        False (default) for a real street address, where the extra context
        already disambiguates correctly and this would only lose the
        building-level precision callers like EnrichmentAgent's
        locality/suburb resolution rely on.
        """
        if not self._api_key or not address:
            return None
        cache_key = ("city:" if prefer_city else "addr:") + " ".join(address.lower().split())
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Geoapify's free plan permits five requests/second; keep a margin.
        wait = 0.22 - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            params = {
                "text": address,
                "filter": "countrycode:in",
                "format": "json",
                "limit": 1,
                "apiKey": self._api_key,
            }
            if prefer_city:
                params["type"] = "city"
            query = urlencode(params)
            request = Request(f"{GEOAPIFY_URL}?{query}", headers={"User-Agent": "MarketingAgent/1.0"})
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self._last_request_at = time.monotonic()
            result = (payload.get("results") or [None])[0]
            if not result or result.get("country_code", "").lower() != "in":
                self._cache[cache_key] = None
                return None
            geocoded = GeocodedAddress(
                city=result.get("city") or result.get("municipality"),
                state=result.get("state"),
                district=result.get("district") or result.get("county"),
                locality=result.get("suburb") or result.get("neighbourhood"),
                location_id=result.get("place_id"),
                latitude=result.get("lat"),
                longitude=result.get("lon"),
            )
            self._cache[cache_key] = geocoded
            return geocoded
        except Exception as exc:
            logger.warning("Geoapify geocoding failed for company address: %s", exc)
            self._cache[cache_key] = None
            return None
