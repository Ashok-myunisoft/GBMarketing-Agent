import re
from difflib import SequenceMatcher
from typing import Optional

# Thresholds for _similar(), calibrated against real place-name pairs: a typo
# or minor spelling variant of the SAME place ("Ahmedabad"/"Ahamedabad",
# "Chennai"/"Channai") scores ~0.85-0.95; two genuinely DIFFERENT places
# ("Ahmedabad"/"Gandhinagar", "Ahmedabad"/"Vadodara") score well under 0.6.
# Below _FUZZY_MATCH_THRESHOLD is treated as confident evidence of a
# different place; the band in between is too ambiguous to assert either way.
_FUZZY_MATCH_THRESHOLD = 0.85
_FUZZY_OUTSIDE_THRESHOLD = 0.6


CITY_ALIASES = {
    "coimbatore": "Coimbatore", "kovai": "Coimbatore",
    "bengaluru": "Bengaluru", "bangalore": "Bengaluru",
    "chennai": "Chennai", "madras": "Chennai", "tiruppur": "Tiruppur",
    "madurai": "Madurai", "salem": "Salem", "erode": "Erode", "hosur": "Hosur",
    "tiruchirappalli": "Tiruchirappalli", "trichy": "Tiruchirappalli",
}

STATE_NAMES = (
    "Tamil Nadu", "Karnataka", "Kerala", "Andhra Pradesh", "Telangana",
    "Maharashtra", "Gujarat", "Rajasthan", "Delhi", "Uttar Pradesh",
    "West Bengal", "Madhya Pradesh", "Bihar", "Odisha", "Punjab",
    "Haryana", "Goa", "Puducherry",
)

# Add localities here as the sales team expands into another target city. A
# locality is never guessed: it is returned only after an exact address match.
def canonical_city(value: Optional[str]) -> Optional[str]:
    text = (value or "").lower()
    return next((city for alias, city in CITY_ALIASES.items() if re.search(r"\b" + re.escape(alias) + r"\b", text)), None)


def _normalize(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _similar(a: str, b: str) -> float:
    """Character-level similarity ratio between two already-normalized strings."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _state_name(value: Optional[str]) -> Optional[str]:
    """Returns the STATE_NAMES entry `value` refers to, if any.

    Tries an exact whole-word match first (the normal case - addresses and
    correctly-spelled requests), then falls back to a fuzzy match so a typo
    in the user's own request ("Gujrat") still resolves to the real state.
    """
    text = (value or "").lower()
    exact = next((name for name in STATE_NAMES if re.search(r"\b" + re.escape(name.lower()) + r"\b", text)), None)
    if exact or not value:
        return exact
    normalized = _normalize(value)
    return next((name for name in STATE_NAMES if _similar(_normalize(name), normalized) >= _FUZZY_MATCH_THRESHOLD), None)


def classify_location(
    company_city: Optional[str],
    company_state: Optional[str],
    company_address: Optional[str],
    requested: Optional[str],
) -> "tuple[str, str]":
    """Generic hierarchy-aware match between a requested location and a company's.

    Returns (decision, reason) with decision one of "match" / "outside" / "unknown".
    Works at whichever granularity the request resolves to - state (any entry in
    STATE_NAMES, not a hardcoded single state) or city/locality - using the
    company's own structured city/state fields (already populated by enrichment's
    geocoding/address parsing) rather than a literal substring of the requested
    name. A company is only ever marked "outside" on positive conflicting
    evidence; missing/unresolvable data always falls back to "unknown" so it can
    be kept and flagged rather than silently dropped.
    """
    requested = (requested or "").strip()
    if not requested:
        return "match", "no location requested"

    requested_state = _state_name(requested)
    if requested_state:
        actual_state = company_state or _state_name(company_address)
        if not actual_state:
            return "unknown", "company state could not be determined"
        if _normalize(actual_state) == _normalize(requested_state):
            return "match", f"company state '{actual_state}' matches requested state '{requested_state}'"
        return "outside", f"resolved state '{actual_state}' does not match requested state '{requested_state}'"

    # Not a recognised state name - treat the request as a city/locality.
    requested_canonical = canonical_city(requested) or _normalize(requested)
    if company_city:
        actual_canonical = canonical_city(company_city) or _normalize(company_city)
        if actual_canonical == requested_canonical or requested_canonical in actual_canonical or actual_canonical in requested_canonical:
            return "match", f"company city '{company_city}' matches requested '{requested}'"
        # A typo or minor spelling variant of the SAME place ("Ahmedabad" vs
        # a user-typed "Ahamedabad") scores high here; two genuinely
        # different places score well below the threshold - see the
        # constants' docstring for the calibration this is based on.
        similarity = _similar(actual_canonical, requested_canonical)
        if similarity >= _FUZZY_MATCH_THRESHOLD:
            return "match", f"company city '{company_city}' closely matches requested '{requested}' (fuzzy)"
        if similarity >= _FUZZY_OUTSIDE_THRESHOLD:
            return "unknown", f"company city '{company_city}' only loosely resembles requested '{requested}'"
        # company_city is a structured field, not free text, so a clear
        # mismatch here is real evidence, not just an absence of a substring.
        return "outside", f"resolved city '{company_city}' does not match requested city '{requested}'"

    # No structured city - only the unstructured address is available, which is
    # too noisy to ever assert "outside" from; a miss just means "unknown".
    normalized_address = _normalize(company_address)
    if normalized_address and requested_canonical in normalized_address:
        return "match", f"requested '{requested}' found in company address"
    return "unknown", "company city could not be determined"


def parse_address_components(address: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not address:
        return None, None
    normalized = re.sub(r"\s+", " ", address).strip()
    city = canonical_city(normalized)
    state = next((name for name in STATE_NAMES if re.search(r"\b" + re.escape(name.lower()) + r"\b", normalized.lower())), None)
    return city, state
