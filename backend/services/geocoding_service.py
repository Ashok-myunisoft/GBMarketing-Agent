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
    region: Optional[str]


class GeoapifyGeocodingService:
    """Rate-limited, cached geocoding for verified company addresses."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.GEOAPIFY_API_KEY
        self._cache: dict[str, Optional[GeocodedAddress]] = {}
        self._last_request_at = 0.0

    def geocode(self, address: Optional[str]) -> Optional[GeocodedAddress]:
        if not self._api_key or not address:
            return None
        cache_key = " ".join(address.lower().split())
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Geoapify's free plan permits five requests/second; keep a margin.
        wait = 0.22 - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        try:
            query = urlencode({
                "text": address,
                "filter": "countrycode:in",
                "format": "json",
                "limit": 1,
                "apiKey": self._api_key,
            })
            request = Request(f"{GEOAPIFY_URL}?{query}", headers={"User-Agent": "MarketingAgent/1.0"})
            with urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self._last_request_at = time.monotonic()
            result = (payload.get("results") or [None])[0]
            if not result or result.get("country_code", "").lower() != "in":
                self._cache[cache_key] = None
                return None
            region = (
                result.get("suburb")
                or result.get("neighbourhood")
                or result.get("district")
                or result.get("city_district")
            )
            geocoded = GeocodedAddress(
                city=result.get("city") or result.get("municipality"),
                state=result.get("state"),
                region=region,
            )
            self._cache[cache_key] = geocoded
            return geocoded
        except Exception as exc:
            logger.warning("Geoapify geocoding failed for company address: %s", exc)
            self._cache[cache_key] = None
            return None
