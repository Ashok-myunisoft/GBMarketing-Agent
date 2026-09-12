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
    # Added from real search-query history (backend/data/jobs.sqlite3) -
    # these were being searched already, just with no seed data at all to
    # protect them, so classify_location's city/state checks silently never
    # applied to them (canonical_city/_known_state_for_request both return
    # None for anything not listed here).
    "hyderabad": "Hyderabad", "hydrabad": "Hyderabad",
    "mumbai": "Mumbai", "bombay": "Mumbai",
    "pune": "Pune",
    "ahmedabad": "Ahmedabad", "ahamedabad": "Ahmedabad",
    "kochi": "Kochi", "cochin": "Kochi",
    "sricity": "Sricity", "shri city": "Sricity", "shree city": "Sricity",
    "kancheepuram": "Kancheepuram", "kanchipuram": "Kancheepuram",
    "sivakasi": "Sivakasi",
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

# CBIC's public GSTIN state-code prefixes (a GSTIN's first 2 digits) - used
# only to demote (never outright reject) a GST candidate whose registered
# state contradicts the company's own known state. A company's GST
# registration state can legitimately differ from its operational address
# for real, non-fraudulent reasons (multi-state registration, a corporate
# office elsewhere), so this is deliberately a soft signal, not a hard rule.
GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh",
    "23": "Madhya Pradesh", "24": "Gujarat", "27": "Maharashtra",
    "28": "Andhra Pradesh", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "36": "Telangana", "37": "Andhra Pradesh",
}

# Real state for each seeded city - used only to catch a company confidently
# placed in a *different* state (e.g. a Delhi or Mumbai listing surfaced by a
# loosely geo-scoped Coimbatore search). STATE_NAMES already covers all of
# India's major states/UTs, so this check catches far more than the small
# hand-picked CITY_ALIASES/CITY_LOCALITIES lists ever could on their own -
# those only recognise conflicting evidence from this same handful of South
# Indian cities, never a company plainly placed anywhere else in the country.
CITY_STATE: dict[str, str] = {
    "Coimbatore": "Tamil Nadu",
    "Chennai": "Tamil Nadu",
    "Bengaluru": "Karnataka",
    "Tiruppur": "Tamil Nadu",
    "Madurai": "Tamil Nadu",
    "Salem": "Tamil Nadu",
    "Erode": "Tamil Nadu",
    "Hosur": "Tamil Nadu",
    "Tiruchirappalli": "Tamil Nadu",
    "Hyderabad": "Telangana",
    "Mumbai": "Maharashtra",
    "Pune": "Maharashtra",
    "Ahmedabad": "Gujarat",
    "Kochi": "Kerala",
    "Sricity": "Andhra Pradesh",
    "Kancheepuram": "Tamil Nadu",
    "Sivakasi": "Tamil Nadu",
}

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


def city_for_locality(locality: Optional[str]) -> Optional[str]:
    """Returns the seeded parent city for a known CITY_LOCALITIES entry.

    Lets a company whose city couldn't be resolved directly (no website, a
    Maps address too vague to geocode) still get a real city - and therefore
    pass through classify_location's district/city checks instead of falling
    through to its much weaker address-substring fallback - whenever a
    locality alone was enough to identify it unambiguously.
    """
    if not locality:
        return None
    return next(
        (city for city, localities in CITY_LOCALITIES.items() if locality in localities),
        None,
    )


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


def _known_state_for_request(requested: str) -> Optional[str]:
    """Best-effort real state for a request that resolves to one of our
    seeded cities/districts/localities - whatever granularity classify_location
    actually matched requested against. Returns None for a request this
    codebase has no seed data for at all (nothing to compare against)."""
    city = canonical_city(requested)
    if city:
        return CITY_STATE.get(city)
    district = canonical_district(requested)
    if district:
        city = next((name for name, dist in CITY_DISTRICTS.items() if dist == district), None)
        return CITY_STATE.get(city) if city else None
    locality = canonical_locality(requested)
    if locality:
        return CITY_STATE.get(city_for_locality(locality) or "")
    return None


def _state_conflict(
    company_state: Optional[str], company_address: Optional[str], requested_state_hint: Optional[str],
) -> Optional[str]:
    """Returns the company's actual state if it confidently differs from
    requested_state_hint, else None (including when either side is unknown).

    Only ever called once classify_location's own city/district/locality
    checks have already come up empty - this is a broader, independent
    signal (STATE_NAMES covers all of India's major states/UTs) that catches
    a company placed somewhere the small CITY_ALIASES/CITY_LOCALITIES seed
    lists were never going to recognise at all (e.g. Delhi, Mumbai).
    """
    if not requested_state_hint:
        return None
    actual_state = company_state or _state_name(company_address)
    if actual_state and _normalize(actual_state) != _normalize(requested_state_hint):
        return actual_state
    return None


def gst_state_conflict(
    gstin: Optional[str], company_state: Optional[str], company_address: Optional[str] = None,
) -> Optional[str]:
    """Returns the GSTIN's registered state if it confidently differs from
    the company's own known state, else None (including when either side
    is unknown/unrecognised).

    Same "only assert on positive conflicting evidence" shape as
    _state_conflict above - a public counterpart for services outside this
    module (GST/turnover candidate ranking) to demote, never outright
    reject, a GSTIN whose state-code prefix contradicts the company's known
    state. This is a soft signal: a company can legitimately hold a GST
    registration in a state other than its operational address.
    """
    if not gstin or len(gstin) < 2:
        return None
    implied_state = GST_STATE_CODES.get(gstin[:2])
    if not implied_state:
        return None
    actual_state = company_state or _state_name(company_address)
    if actual_state and _normalize(actual_state) != _normalize(implied_state):
        return implied_state
    return None


def classify_location(
    company_city: Optional[str],
    company_state: Optional[str],
    company_address: Optional[str],
    requested: Optional[str],
    company_district: Optional[str] = None,
    company_locality: Optional[str] = None,
    requested_geocoded_state: Optional[str] = None,
    requested_geocoded_district: Optional[str] = None,
    requested_geocoded_city: Optional[str] = None,
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

    The three ``requested_geocoded_*`` parameters are optional overrides -
    typically the caller's own one-time geocoding of ``requested`` (e.g. via
    GeoapifyGeocodingService), not this module's own lookup. They let this
    function work at full district/city precision for a requested location
    outside the small hand-picked CITY_ALIASES/CITY_DISTRICTS/CITY_LOCALITIES
    seed lists (e.g. "Nagpur", never seeded here) instead of only the broader,
    state-level fallback those lists would otherwise leave it to. Omit them
    (the default) to get exactly the seed-list-only behaviour this function
    always had.
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

    # Best-effort real state for whatever the request resolves to below -
    # checked only once every more specific city/district/locality check has
    # come up empty. The caller's geocoded override always wins when given
    # (it's real, authoritative data for literally any place); otherwise this
    # falls back to the seed lists, exactly as before - STATE_NAMES covers
    # far more ground than CITY_ALIASES/CITY_LOCALITIES ever could alone (a
    # Delhi or Mumbai listing surfaced by a loosely geo-scoped Coimbatore
    # search names a state those lists were never going to recognise at all).
    requested_state_hint = requested_geocoded_state or _known_state_for_request(requested)

    # A known district matches a company confirmed in that district, or a
    # city whose trusted seed parent is that district. Same override
    # precedence as the state hint above - a geocoded district for an
    # unseeded requested city (e.g. "Nagpur") lets this branch apply at full
    # district precision instead of only ever reaching the broader state
    # fallback inside it.
    requested_district = canonical_district(requested) or requested_geocoded_district
    if requested_district:
        # company_district is a structured field (the same trust level the
        # plain-city branch below already gives company_city) - not just
        # canonicalized against the 9-entry CITY_DISTRICTS seed list, which
        # would otherwise silently refuse to recognise a real, already-
        # geocoded district like "Pune" or "Nagpur" purely because it was
        # never hand-added there. Falling back to canonical_district(...)
        # first still normalizes a known district's spelling variants.
        actual_district = canonical_district(company_district) or company_district
        if not actual_district and company_city:
            actual_district = CITY_DISTRICTS.get(canonical_city(company_city) or company_city)
        if not actual_district:
            # No structured city either - fall back to whatever city/locality
            # the free-text address itself names, same reasoning as the
            # unstructured-address fallback below: a *different* known place
            # actually named in the address is real conflicting evidence, not
            # just an absence of the requested one.
            address_city = canonical_city(company_address)
            if not address_city:
                address_city = city_for_locality(canonical_locality(company_address))
            if address_city:
                actual_district = CITY_DISTRICTS.get(address_city)
                if not actual_district and requested_geocoded_city and _normalize(address_city) != _normalize(requested_geocoded_city):
                    # address_city is seeded (CITY_ALIASES) but CITY_DISTRICTS
                    # was never taught its district (true for most cities added
                    # from real query history, e.g. Pune, Hyderabad) - a direct
                    # city-vs-city mismatch against the geocoded request is
                    # still real evidence even without that district entry.
                    # Matters before enrichment has run at all (GoogleMapsProvider's
                    # search-time filter): only raw address text is available then,
                    # never a geocoded/structured company field.
                    return "outside", (
                        f"address names '{address_city}', not requested district's city '{requested_geocoded_city}'"
                    )
        if actual_district:
            if _normalize(actual_district) == _normalize(requested_district):
                return "match", f"company district '{actual_district}' matches requested district '{requested_district}'"
            return "outside", f"resolved district '{actual_district}' does not match requested district '{requested_district}'"
        conflicting_state = _state_conflict(company_state, company_address, requested_state_hint)
        if conflicting_state:
            return "outside", (
                f"resolved state '{conflicting_state}' does not match '{requested_state_hint}' "
                f"(state of requested district '{requested_district}')"
            )
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
        # No specific locality match either way - but the *city* the address
        # or structured field actually names is still real evidence, even
        # without a seeded locality hit. Needed for a requested city whose
        # district name differs from the city name (Bengaluru, Hosur):
        # canonical_district(requested) doesn't match "Peenya, Bengaluru" at
        # all, landing here instead of the district branch above - without
        # this, a different city plainly named in the address (e.g. a
        # Chennai company surfaced by a Bengaluru locality search) would
        # only ever come back "unknown", never "outside".
        requested_locality_city = city_for_locality(requested_locality)
        address_city = actual_city or canonical_city(company_address)
        if requested_locality_city and address_city and _normalize(address_city) != _normalize(requested_locality_city):
            return "outside", (
                f"resolved city '{address_city}' does not match '{requested_locality_city}' "
                f"(parent city of requested locality '{requested_locality}')"
            )
        conflicting_state = _state_conflict(company_state, company_address, requested_state_hint)
        if conflicting_state:
            return "outside", (
                f"resolved state '{conflicting_state}' does not match '{requested_state_hint}' "
                f"(state of requested locality '{requested_locality}')"
            )
        return "unknown", "company locality could not be determined"

    # Not a recognised state, district, or locality - treat the request as a city.
    # Normalized even when canonical_city() (or the geocoded override)
    # already resolved a proper-cased name (e.g. "Ahmedabad") - comparing
    # that directly against an unseeded side's lowercase _normalize()
    # fallback would make the equality/substring/fuzzy checks below silently
    # case-sensitive, weakening every comparison purely because one side
    # happened to be in CITY_ALIASES and the other wasn't.
    requested_canonical = _normalize(canonical_city(requested) or requested_geocoded_city or requested)
    if company_city:
        actual_canonical = _normalize(canonical_city(company_city) or company_city)
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
    # too noisy to ever assert "outside" from an absence; but a *different*,
    # confidently-identified city or locality actually named in the address is
    # real conflicting evidence (e.g. a Chennai listing surfaced by a loosely
    # geo-scoped Coimbatore search) - only known seeded names are trusted for
    # this, never an arbitrary substring, so this can't misfire on a city this
    # codebase has no reference data for.
    normalized_address = _normalize(company_address)
    if normalized_address and requested_canonical in normalized_address:
        return "match", f"requested '{requested}' found in company address"

    address_city = canonical_city(company_address)
    if address_city and _normalize(address_city) != requested_canonical:
        return "outside", f"address names '{address_city}', not requested '{requested}'"

    address_locality = canonical_locality(company_address)
    if address_locality:
        locality_city = next(
            (city for city, localities in CITY_LOCALITIES.items() if address_locality in localities),
            None,
        )
        if locality_city and _normalize(locality_city) != requested_canonical:
            return "outside", (
                f"address names locality '{address_locality}' (in {locality_city}), "
                f"not requested '{requested}'"
            )

    conflicting_state = _state_conflict(company_state, company_address, requested_state_hint)
    if conflicting_state:
        return "outside", f"resolved state '{conflicting_state}' does not match '{requested_state_hint}' (state of requested '{requested}')"

    return "unknown", "company city could not be determined"


def parse_address_components(address: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not address:
        return None, None
    normalized = re.sub(r"\s+", " ", address).strip()
    city = canonical_city(normalized)
    state = next((name for name in STATE_NAMES if re.search(r"\b" + re.escape(name.lower()) + r"\b", normalized.lower())), None)
    return city, state
