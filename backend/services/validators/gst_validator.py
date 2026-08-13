"""Thin adapter over the existing GSTIN checksum validator - no new logic.
Rejects an LLM-extracted GST number that merely looks shaped like a GSTIN
but fails the check digit, same rule every other GST source tier already
relies on (services/gst_turnover_enrichment/gst_extraction.py)."""

from typing import Optional

from services.gst_turnover_enrichment.gst_extraction import find_valid_gstin


def validate(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return find_valid_gstin(value.upper())
