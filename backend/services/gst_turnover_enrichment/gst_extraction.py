"""
Shared GSTIN pattern + checksum validator, reused by every GST source tier
(website, PDF, annual report - all Firecrawl-fetched) so a candidate is
never accepted purely on regex shape - the same checksum check
enrichment_agent.py used before this module existed.
"""

import re
from typing import Optional

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def find_valid_gstin(text: Optional[str]) -> Optional[str]:
    candidates = find_valid_gstins(text)
    return candidates[0] if candidates else None


def find_valid_gstins(text: Optional[str]) -> list[str]:
    """Return every distinct checksum-valid GSTIN in source order.

    A page can legitimately contain registrations for more than one state;
    callers must rank candidates rather than accepting the first match.
    """
    valid: list[str] = []
    for raw in GSTIN_PATTERN.findall(text or ""):
        gst = raw.upper()
        total, factor = 0, 1
        for char in gst[:-1]:
            value = GSTIN_CHARSET.index(char) * factor
            total += value // 36 + value % 36
            factor = 2 if factor == 1 else 1
        expected = GSTIN_CHARSET[(36 - total % 36) % 36]
        if gst[-1] == expected:
            valid.append(gst)
    return list(dict.fromkeys(valid))
