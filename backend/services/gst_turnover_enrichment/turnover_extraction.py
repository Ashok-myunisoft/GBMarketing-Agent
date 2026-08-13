"""
Best-effort turnover finder for free text (a rendered page or a PDF's
extracted text) - looks for a currency amount sitting near a turnover/
revenue context word, and an associated financial year if one is nearby.

This never estimates or infers a figure: if no amount co-occurs with a
turnover/revenue context word, nothing is returned (Step 6's "do not
estimate, do not hallucinate - if no trustworthy turnover exists, return
null").
"""

import re
from typing import Optional, TypedDict

_CONTEXT = re.compile(
    r"(revenue\s+from\s+operations|annual\s+turnover|total\s+turnover|turnover|total\s+revenue|annual\s+revenue|revenue|total\s+income)",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d+)?)\s*(crore|cr\.?|lakh|lac|lakhs|million|mn|billion|bn)\b",
    re.IGNORECASE,
)
_FINANCIAL_YEAR = re.compile(r"\b(?:FY\s*)?(\d{4})\s*[-/]\s*(\d{2,4})\b", re.IGNORECASE)

_MAX_CANDIDATES = 5
_WINDOW_LINES = 2
_NON_TURNOVER_CONTEXT = re.compile(
    r"\b(project|order|contract|market\s+size|investment|paid[- ]up|authorized)\b",
    re.IGNORECASE,
)


class TurnoverCandidate(TypedDict):
    value: str
    currency: Optional[str]
    financial_year: Optional[str]
    metric: str


def find_turnover_candidates(text: Optional[str], max_candidates: int = _MAX_CANDIDATES) -> list[TurnoverCandidate]:
    if not text:
        return []

    lines = text.splitlines()
    candidates: list[TurnoverCandidate] = []
    seen: set[tuple[str, Optional[str], str]] = set()

    for index, line in enumerate(lines):
        start = max(0, index - _WINDOW_LINES)
        window = " ".join(lines[start:index + _WINDOW_LINES + 1])
        # Financial tables commonly put the metric in a heading and values
        # for several financial years on following rows.  Keep the context
        # window bounded so a nearby profit/PAT line cannot become turnover.
        if not _CONTEXT.search(window):
            continue
        # A monetary amount beside "turnover" can still refer to a project,
        # order, market or capital figure.  Those are not company turnover.
        if _NON_TURNOVER_CONTEXT.search(window):
            continue

        amount_match = _AMOUNT.search(line) or _AMOUNT.search(window)
        if not amount_match:
            continue

        value_number, unit = amount_match.groups()
        value = f"{value_number.strip()} {unit.strip().title()}"

        fy_match = _FINANCIAL_YEAR.search(line) or _FINANCIAL_YEAR.search(window)
        financial_year = None
        if fy_match:
            start_year, end_year = fy_match.groups()
            financial_year = f"{start_year}-{end_year[-2:]}"

        metric_match = _CONTEXT.search(window)
        metric = metric_match.group(1).strip().title() if metric_match else "Turnover"
        key = (value, financial_year, metric)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"value": value, "currency": "INR", "financial_year": financial_year, "metric": metric})

        if len(candidates) >= max_candidates:
            break

    # When a source lists several years, prefer the latest labelled year and
    # the least ambiguous metric.  Nothing is inferred: all values remain
    # candidates for normal source/confidence validation upstream.
    metric_priority = {
        "Revenue From Operations": 3,
        "Annual Turnover": 2,
        "Total Turnover": 2,
        "Turnover": 2,
        "Total Revenue": 1,
        "Annual Revenue": 1,
        "Revenue": 1,
        "Total Income": 0,
    }
    return sorted(
        candidates,
        key=lambda item: (item["financial_year"] or "", metric_priority.get(item["metric"], 0)),
        reverse=True,
    )
