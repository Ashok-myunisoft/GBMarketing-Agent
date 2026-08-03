import re
from typing import Optional


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


def matches_requested_city(city: Optional[str], address: Optional[str], requested: Optional[str]) -> bool:
    """Returns true only when a geocoded/verified address matches the request city."""
    requested_canonical = canonical_city(requested)
    actual_canonical = canonical_city(city) or canonical_city(address)
    if requested_canonical:
        return actual_canonical == requested_canonical
    normalized_requested = re.sub(r"[^a-z0-9]", "", (requested or "").lower())
    if not normalized_requested:
        return False
    # Outside the curated alias list there is no canonical form to compare, so
    # fall back to a substring check - and check address too, since several
    # providers only ever populate address, never a clean city field.
    return any(
        normalized_requested in re.sub(r"[^a-z0-9]", "", (candidate or "").lower())
        for candidate in (city, address)
    )


def parse_address_components(address: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if not address:
        return None, None, None
    normalized = re.sub(r"\s+", " ", address).strip()
    city = canonical_city(normalized)
    state = next((name for name in STATE_NAMES if re.search(r"\b" + re.escape(name.lower()) + r"\b", normalized.lower())), None)
    # Region is intentionally not inferred locally. Geoapify provides the
    # locality/neighbourhood component from its geocoded address response.
    return city, state, None
