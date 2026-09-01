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

# Seed locality/industrial-area list for the cities already covered by
# CITY_ALIASES. Used only to fan a single "<industry> <city>" search query
# out into several "<industry> <locality>, <city>" queries (see
# services/search_services.py) so Google Maps/TradeIndia/Tavily each surface
# a broader, non-identical slice of businesses for that city instead of
# whatever one city-wide query happens to rank first - those platforms don't
# expose a "give me everything" mode, only "give me your top results for
# this query", so more distinctly-worded queries is the lever available.
#
# This is a starting seed, not exhaustive - expand it the same way as
# CITY_ALIASES: add localities here as the sales team identifies more for a
# target city. A city missing from this table (or not yet in CITY_ALIASES)
# simply searches as a single query, exactly as before this table existed.
CITY_LOCALITIES: dict[str, list[str]] = {
    "Coimbatore": ["Peelamedu", "Ganapathy", "Saravanampatti", "Kalapatti", "Singanallur"],
    "Chennai": ["Ambattur", "Guindy", "Perungudi", "Sriperumbudur", "Ekkatuthangal"],
    "Bengaluru": ["Peenya", "Jigani", "Electronic City", "Whitefield", "Bommasandra"],
    "Tiruppur": ["Kumar Nagar", "Veerapandi", "Avinashi Road"],
    "Madurai": ["Tallakulam", "Goripalayam", "K. Pudur"],
    "Salem": ["Ammapet", "Hasthampatti", "Suramangalam"],
    "Erode": ["Perundurai", "Erode SIPCOT"],
    "Hosur": ["Hosur SIPCOT", "Bommasandra Industrial Area"],
    "Tiruchirappalli": ["BHEL Township", "Thillai Nagar", "Srirangam"],
}

# The static hierarchy is deliberately a small, high-confidence fallback.
# Dynamic providers can add localities at search time, but validation must not
# infer a parent relationship it cannot prove.  Add cities/districts here as
# the supported sales geography grows, or replace this seed with an imported
# administrative dataset.
CITY_DISTRICTS: dict[str, str] = {
    "Coimbatore": "Coimbatore",
    "Chennai": "Chennai",
    "Bengaluru": "Bengaluru Urban",
    "Tiruppur": "Tiruppur",
    "Madurai": "Madurai",
    "Salem": "Salem",
    "Erode": "Erode",
    "Hosur": "Krishnagiri",
    "Tiruchirappalli": "Tiruchirappalli",
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


def canonical_locality(value: Optional[str], city: Optional[str] = None) -> Optional[str]:
    """Return a known locality only when its parent city is unambiguous.

    A locality name can occur in more than one city.  When the caller has a
    city, restrict matching to that city; otherwise return a locality only if
    it appears under exactly one seeded city.  This preserves the validation
    rule that missing evidence is ``unknown``, never a guessed match.
    """
    text = (value or "").lower()
    candidate_cities = [city] if city else list(CITY_LOCALITIES)
    matches = []
    for candidate_city in candidate_cities:
        for locality in CITY_LOCALITIES.get(candidate_city or "", []):
            if re.search(r"\b" + re.escape(locality.lower()) + r"\b", text):
                matches.append(locality)
    return matches[0] if len(set(matches)) == 1 else None


def canonical_district(value: Optional[str]) -> Optional[str]:
    """Resolve one of the districts represented by the trusted seed data."""
    text = (value or "").lower()
    return next(
        (district for district in dict.fromkeys(CITY_DISTRICTS.values())
         if re.search(r"\b" + re.escape(district.lower()) + r"\b", text)),
        None,
    )


def location_query_variants(location: Optional[str]) -> "list[Optional[str]]":
    """Expands a requested location into itself plus its known localities
    (CITY_LOCALITIES), e.g. "Coimbatore" -> ["Coimbatore", "Peelamedu,
    Coimbatore", "Ganapathy, Coimbatore", ...], so a search can be fanned out
    across several distinctly-worded queries for the same city instead of
    just one.

    Falls back to [location] - a single-element list, unchanged behaviour -
    whenever the location is empty or its city isn't in CITY_LOCALITIES yet.
    """
    if not location or not location.strip():
        return [location]
    city = canonical_city(location)
    localities = CITY_LOCALITIES.get(city, []) if city else []
    if not localities:
        return [location]
    return [location] + [f"{locality}, {city}" for locality in localities]


def _normalize(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def hierarchy_ids(
    state: Optional[str],
    district: Optional[str],
    city: Optional[str],
    locality: Optional[str],
) -> dict[str, Optional[str]]:
    """Build deterministic IDs for the resolved hierarchy.

    These IDs are intentionally derived only from resolved components.  They
    make records comparable within this application today and can later be
    replaced by source-native administrative IDs without changing callers.
    """
    state_key = _normalize(_state_name(state) or state)
    district_key = _normalize(canonical_district(district) or district)
    city_key = _normalize(canonical_city(city) or city)
    locality_key = _normalize(canonical_locality(locality, canonical_city(city)) or locality)

    state_id = f"in.state.{state_key}" if state_key else None
    district_id = f"{state_id}.district.{district_key}" if state_id and district_key else None
    city_id = f"{district_id}.city.{city_key}" if district_id and city_key else None
    location_id = f"{city_id}.locality.{locality_key}" if city_id and locality_key else city_id
    return {
        "state_id": state_id,
        "district_id": district_id,
        "city_id": city_id,
        "location_id": location_id,
    }


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
    company_district: Optional[str] = None,
    company_locality: Optional[str] = None,
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

    # A known district matches a company confirmed in that district, or a
    # city whose trusted seed parent is that district.
    requested_district = canonical_district(requested)
    if requested_district:
        actual_district = canonical_district(company_district)
        if not actual_district and company_city:
            actual_district = CITY_DISTRICTS.get(canonical_city(company_city) or company_city)
        if actual_district:
            if _normalize(actual_district) == _normalize(requested_district):
                return "match", f"company district '{actual_district}' matches requested district '{requested_district}'"
            return "outside", f"resolved district '{actual_district}' does not match requested district '{requested_district}'"
        return "unknown", "company district could not be determined"

    # A known locality is more specific than a city.  A city-level record is
    # insufficient to confirm it, but an explicitly different locality in the
    # same city is reliable conflicting evidence.
    requested_locality = canonical_locality(requested)
    if requested_locality:
        actual_city = canonical_city(company_city)
        actual_locality = canonical_locality(company_locality, actual_city) or canonical_locality(company_address, actual_city)
        if actual_locality:
            if _normalize(actual_locality) == _normalize(requested_locality):
                return "match", f"company locality '{actual_locality}' matches requested locality '{requested_locality}'"
            return "outside", f"resolved locality '{actual_locality}' does not match requested locality '{requested_locality}'"
        return "unknown", "company locality could not be determined"

    # Not a recognised state, district, or locality - treat the request as a city.
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
