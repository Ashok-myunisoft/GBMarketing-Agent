"""Formatting for the single location field in Marketing lead storage."""


def marketing_location(company) -> str | None:
    """Return locality plus parent city for storage/export when both exist."""
    locality = (getattr(company, "locality", None) or "").strip()
    city = (getattr(company, "city", None) or "").strip()
    if locality and city and locality.casefold() not in city.casefold():
        return f"{locality}, {city}"
    return city or locality or None
