"""Shared identity-key normalization for search and validation deduplication."""

import re
from urllib.parse import urlparse

_GENERIC_MAPS_LABELS = {
    "google maps", "search google maps", "google maps result",
    "google maps listing", "google maps business",
}


def is_generic_maps_label(value: str | None) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()
    return normalized in _GENERIC_MAPS_LABELS


def company_composite_key(
    company,
    *,
    prefer_source_locations: bool = False,
    normalize_legal_suffixes: bool = False,
) -> str | None:
    """Build the duplicate key from company name, industry, and location only."""
    def normalize(value: str | None) -> str:
        return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())

    company_name = company.company_name or ""
    if normalize_legal_suffixes:
        # Treat common spellings of the same Indian company suffix alike for
        # discovery-row dedup only (e.g. "Pvt. Ltd." and "Private Limited").
        company_name = re.sub(r"\bpvt\b", "private", company_name, flags=re.IGNORECASE)
        company_name = re.sub(r"\bltd\b", "limited", company_name, flags=re.IGNORECASE)
    name = normalize(company_name)
    industry = normalize(company.industry)
    source_locations = (
        (company.source_metadata or {}).get("_source_locations")
        if prefer_source_locations else None
    )
    if isinstance(source_locations, (list, tuple)):
        location_value = " ".join(str(value) for value in source_locations if value)
    elif isinstance(source_locations, str):
        location_value = source_locations
    else:
        location_value = (
            company.city or company.locality or company.district
            or company.state or company.address
        )
    location = normalize(location_value)
    if not (name and industry and location):
        return None
    return f"company:{name}|industry:{industry}|location:{location}"


def company_identity_keys(company, *, include_name: bool = True) -> set[str]:
    """Return exact normalized identity keys; names are optional and exact only."""
    keys: set[str] = set()
    if company.gst:
        value = re.sub(r"\W+", "", company.gst).upper()
        if value:
            keys.add(f"gst:{value}")
    if company.website:
        parsed = urlparse(company.website if "://" in company.website else f"https://{company.website}")
        host = parsed.netloc.lower().removeprefix("www.")
        if host and "tradeindia.com" not in host:
            keys.add(f"website:{host}")
    if company.email:
        value = company.email.strip().lower()
        if value:
            keys.add(f"email:{value}")
    for number in (company.phone, company.phone_alt):
        if number:
            digits = re.sub(r"\D+", "", number)
            if len(digits) >= 7:
                keys.add(f"phone:{digits[-10:]}")
    if include_name:
        name = re.sub(r"\W+", "", company.company_name).lower()
        if name:
            keys.add(f"name:{name}")
    return keys


def search_dedup_keys(company) -> set[str]:
    """Search dedup uses identifiers only, never a name-only/similarity match.

    Incomplete phone fragments are omitted here because short numbers are
    especially collision-prone. Validation retains its established policy.
    """
    keys = company_identity_keys(company, include_name=False)
    return {
        key for key in keys
        if not (key.startswith("phone:") and len(key.removeprefix("phone:")) < 10)
    }
