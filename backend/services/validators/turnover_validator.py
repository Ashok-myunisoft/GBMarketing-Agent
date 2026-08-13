"""
Normalizes an LLM- or regex-extracted turnover value into the same
free-text convention already used throughout this app (e.g. "25 Crore",
"250 Million", "USD 5 Million"). Builds on
config.targeting.parse_turnover_range for range/number recognition rather
than reimplementing amount parsing - this only reformats the unit spelling
and currency prefix, it does not change ValidationAgent's downstream
MIN_TURNOVER_CR comparison, which already tolerates this free-text style.
"""

import re
from typing import Optional

from config.targeting import parse_turnover_range

_UNIT_CANONICAL = {
    "cr": "Crore", "crore": "Crore", "crores": "Crore",
    "lac": "Lakh", "lakh": "Lakh", "lakhs": "Lakh",
    "mn": "Million", "m": "Million", "million": "Million",
    "bn": "Billion", "billion": "Billion",
}
_CURRENCY_PREFIX = re.compile(r"^(usd|inr|rs\.?|₹)\s*", re.IGNORECASE)
_AMOUNT_UNIT = re.compile(
    r"([\d,]+(?:\.\d+)?)\s*(crore|crores|cr|lakh|lakhs|lac|million|mn|m|billion|bn)\b",
    re.IGNORECASE,
)


def normalize(value: Optional[str]) -> Optional[str]:
    """Returns a normalized "<currency prefix?> <number> <Unit>" string, or
    None if no recognizable turnover figure is present in `value`."""

    if not value or not value.strip():
        return None

    text = value.strip()
    currency_match = _CURRENCY_PREFIX.match(text)
    currency = currency_match.group(1).upper().rstrip(".") if currency_match else None
    if currency in {"RS", "INR", "₹"}:
        # Rupees are the implicit default this app already uses everywhere
        # else (gst_turnover_enrichment/turnover_extraction.py never prefixes
        # a Crore/Lakh figure with a currency), so only a genuinely foreign
        # currency (USD, etc.) is worth calling out explicitly.
        currency = None

    amount_match = _AMOUNT_UNIT.search(text)
    if amount_match:
        number, unit = amount_match.groups()
        canonical_unit = _UNIT_CANONICAL.get(unit.lower(), unit.title())
        normalized = f"{number.strip()} {canonical_unit}"
        return f"{currency} {normalized}" if currency else normalized

    # Falls back to the existing slab/figure parser (handles GST-portal
    # wording like "5 Cr to 25 Cr" or "Above 500 Cr") purely to confirm this
    # text is turnover-shaped at all; if even that finds no number, reject it
    # rather than pass through unrecognizable text.
    low, _high = parse_turnover_range(text)
    return text if low is not None else None
