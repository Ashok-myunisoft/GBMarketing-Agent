"""Authorized MCA company-data enrichment through FileSure."""

import json
import logging
from dataclasses import dataclass
from typing import Optional
from urllib.request import Request, urlopen

from core.config import settings

logger = logging.getLogger(__name__)
FILESURE_COMPANY_URL = "https://api.filesure.in/v1/companies/{}"


@dataclass(frozen=True)
class FileSureCompanyData:
    address: Optional[str]
    gst: Optional[str]
    contact_person: Optional[str]
    designation: Optional[str]


class FileSureService:
    """Fetches MCA-backed data only when a CIN/LLPIN is already known."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or settings.FILESURE_API_KEY
        self._cache: dict[str, Optional[FileSureCompanyData]] = {}

    def lookup(self, cin: Optional[str]) -> Optional[FileSureCompanyData]:
        if not self._api_key or not cin:
            return None
        normalized = cin.strip().upper()
        if normalized in self._cache:
            return self._cache[normalized]
        try:
            request = Request(
                FILESURE_COMPANY_URL.format(normalized),
                headers={"x-api-key": self._api_key, "User-Agent": "MarketingAgent/1.0"},
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            master = (payload.get("data") or {}).get("masterData") or {}
            company = master.get("companyData") or {}
            common = master.get("commonData") or {}
            address = self._first_text(company, "MCAMDSCompanyAddress", "registeredAddress", "registeredOfficeAddress", "address")
            address = address or self._first_text(common, "companyAddress", "address")
            gst = self._first_text(company, "gstin", "gst", "gstNumber")
            director = self._first_director(master.get("directorData") or [])
            result = FileSureCompanyData(address, gst, *(director or (None, None)))
            self._cache[normalized] = result
            return result
        except Exception as exc:
            logger.warning("FileSure lookup failed for %s: %s", normalized, exc)
            self._cache[normalized] = None
            return None

    @staticmethod
    def _first_text(data: dict, *keys: str) -> Optional[str]:
        for key in keys:
            value = data.get(key)
            if value and str(value).strip():
                return str(value).strip()
        return None

    @classmethod
    def _first_director(cls, directors: list | dict) -> Optional[tuple[str, str]]:
        if isinstance(directors, dict):
            directors = directors.get("directors") or directors.get("data") or []
        for director in directors:
            if not isinstance(director, dict):
                continue
            name = cls._first_text(director, "directorName", "name", "fullName")
            if not name:
                name = " ".join(
                    str(director.get(key, "")).strip()
                    for key in ("FirstName", "MiddleName", "LastName")
                    if str(director.get(key, "")).strip()
                ) or None
            designation = cls._first_text(director, "designation", "role", "MCAUserRole") or "Director"
            if name:
                return name, designation
        return None
