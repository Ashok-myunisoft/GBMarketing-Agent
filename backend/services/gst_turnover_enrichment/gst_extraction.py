"""
Shared GSTIN pattern + checksum validator, reused by every GST source tier
(website, PDF, annual report - fetched via Crawl4AI, discovered via SearXNG)
so a candidate is never accepted purely on regex shape - the same checksum
check enrichment_agent.py used before this module existed.
"""

import re
from typing import Optional

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.IGNORECASE)
GSTIN_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def find_valid_gstin(text: Optional[str]) -> Optional[str]:
    candidates = find_valid_gstins(text)
    return candidates[0] if candidates else None


def _checksum_ok(gst: str) -> bool:
    total, factor = 0, 1
    for char in gst[:-1]:
        value = GSTIN_CHARSET.index(char) * factor
        total += value // 36 + value % 36
        factor = 2 if factor == 1 else 1
    expected = GSTIN_CHARSET[(36 - total % 36) % 36]
    return gst[-1] == expected


def find_valid_gstins(text: Optional[str]) -> list[str]:
    """Return every distinct checksum-valid GSTIN in source order.

    A page can legitimately contain registrations for more than one state;
    callers must rank candidates rather than accepting the first match.
    """
    valid: list[str] = []
    for raw in GSTIN_PATTERN.findall(text or ""):
        gst = raw.upper()
        if _checksum_ok(gst):
            valid.append(gst)
    return list(dict.fromkeys(valid))


def find_valid_gstins_with_evidence(text: Optional[str]) -> "list[tuple[str, str]]":
    """Same as find_valid_gstins, but each distinct GSTIN also carries a
    short surrounding-text snippet (~1-2 lines, truncated to 300 chars).

    Multiple checksum-valid GSTINs can appear on one page (a directory
    listing the target company alongside a supplier/customer, for
    instance) - the bare value alone can't say whose it is; this snippet
    is what lets a caller (ai_validator.py) judge that from context instead
    of trusting every valid-format match equally.
    """
    text = text or ""
    lines = text.splitlines()
    seen: set = set()
    out: "list[tuple[str, str]]" = []
    for match in GSTIN_PATTERN.finditer(text):
        gst = match.group().upper()
        if gst in seen or not _checksum_ok(gst):
            continue
        seen.add(gst)
        line_index = text.count("\n", 0, match.start())
        window = " ".join(lines[max(0, line_index - 1):line_index + 2]).strip()
        out.append((gst, window[:300]))
    return out
