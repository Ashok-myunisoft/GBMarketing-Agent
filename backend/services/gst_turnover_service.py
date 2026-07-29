"""Aggregate-turnover lookup by GSTIN through gst.jamku.app's RapidAPI listing.

The GST portal itself never exposes an exact turnover figure to the public
(confirmed on jamku's own docs page - that's the reason "Aggregate Turnover"
is called out as special: the official portal withholds it without login).
Every downstream API built on that data, including this one, can therefore
only return a turnover *slab* (e.g. "5 Cr to 25 Cr"), never an exact number.
Callers must treat the result as a range, not a point value.
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen

from config.targeting import parse_turnover_range
from core.config import settings

logger = logging.getLogger(__name__)
GST_TURNOVER_URL = "https://gst-return-status.p.rapidapi.com/free/gstin/{}"
RAPIDAPI_HOST = "gst-return-status.p.rapidapi.com"


@dataclass(frozen=True)
class TurnoverSlab:
    label: str
    min_cr: Optional[float]
    max_cr: Optional[float]


class GstTurnoverService:
    """Fetches the aggregate-turnover slab for a GSTIN, cached per GSTIN."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.RAPIDAPI_KEY
        self._cache: dict[str, Optional[TurnoverSlab]] = {}

    def lookup(self, gstin: Optional[str]) -> Optional[TurnoverSlab]:
        if not self._api_key or not gstin:
            return None
        normalized = gstin.strip().upper()
        if normalized in self._cache:
            return self._cache[normalized]
        try:
            request = Request(
                GST_TURNOVER_URL.format(normalized),
                headers={
                    "content-type": "application/json",
                    "x-rapidapi-key": self._api_key,
                    "x-rapidapi-host": RAPIDAPI_HOST,
                },
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") or payload
            label = (data.get("aggreTurnOver") or "").strip()
            if not label:
                self._cache[normalized] = None
                return None
            min_cr, max_cr = parse_turnover_range(label)
            slab = TurnoverSlab(label, min_cr, max_cr)
            self._cache[normalized] = slab
            return slab
        except Exception as exc:
            logger.warning("gst.jamku.app turnover lookup failed for %s: %s", normalized, exc)
            self._cache[normalized] = None
            return None
