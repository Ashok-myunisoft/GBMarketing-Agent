"""Centralized header alias/normalization registry for the Excel exporter.

The Excel TEMPLATE controls output column order and structure (see
template_reader.py) - this module only answers a narrower question:
"which extracted Company field, if any, does a given header string mean?"

Since services/excel_template/template_mapping.py was added, this module
is the FALLBACK used only when a template has no hidden mapping sheet of
its own - i.e. older templates, or ones simple enough that plain header-
name matching is all they need. A template with a mapping sheet declares
its own per-column mapping explicitly (including things this registry
cannot express at all, like a column that should always hold a fixed
value) and that sheet takes over entirely for that template; see
template_mapping.py's module docstring for the full contract.

Adding a new header spelling for an existing field is a one-line edit to
FIELD_ALIASES below; it never requires touching the matching algorithm or
the export logic in agents/export_agent.py.

"extracted_on" is not a Company field - it is a small, explicitly-known
computed column (today's export timestamp) that a template may optionally
include, handled the same way as any other mapped column.
"""

from __future__ import annotations

import re

# internal field name -> every header spelling seen/expected for it.
# The field name itself (normalized) is always implicitly a valid alias,
# so it never needs to be repeated in its own list.
FIELD_ALIASES: dict[str, list[str]] = {
    "company_name": [
        "Company Name", "Company", "Name of Company", "Business Name",
        "Organisation Name", "Organization Name",
    ],
    "gst": ["GST", "GST Number", "GST No", "GST No.", "GSTIN"],
    "turnover": ["Turn Over", "Turnover", "Annual Turnover"],
    "region": ["Region", "Zone"],
    "city": ["City", "Location", "Town"],
    "industry": ["Industry Type", "Industry", "Sector", "Business Category"],
    "contact_person": ["Contact Person", "Contact Name", "POC", "Point of Contact"],
    "designation": ["Designation", "Job Title", "Title"],
    "phone": [
        "Mobile Number", "Mobile", "Phone", "Contact Number", "Phone Number",
        "Contact Mobile No",
    ],
    "phone_alt": [
        "Alternate Mobile Number", "Alternate Mobile", "Alternate Phone",
        "Secondary Contact Number", "Alternate Contact Number",
    ],
    "email": ["Email ID", "Email", "Email Address", "Contact Email"],
    "address": ["Address1", "Address", "Address Line 1", "Registered Address"],
    "linkedin_url": ["LinkedIN Id", "LinkedIn Id", "LinkedIn", "LinkedIn URL", "LinkedIn Profile"],
    "website": ["Website URL", "Website", "Web Site", "URL"],
    "remarks": ["Remarks", "Notes", "Comments"],
    "followup": ["Followup", "Follow Up", "Follow-up"],
    "extracted_on": ["Extracted On", "Extraction Date", "Date Extracted", "Export Date"],
}

# Columns seen in production templates (e.g. LeadCommonImportDTO's "Lead
# Name", "Description", "Stage", "Incharge", "Alloted To", "Source Detail")
# are deliberately NOT listed above: Company has no corresponding field for
# any of them, and this registry never invents one. They are left for
# template_reader to report as unmapped so the exporter leaves the cell
# blank and logs a warning, rather than guessing.


def normalize(text: str) -> str:
    """Collapse a header string to a comparable form (lowercase, alnum only).

    "Company Name", "company_name", "COMPANY NAME", "CompanyName" and
    "Company  Name" all normalize to the same string.
    """
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _build_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field, aliases in FIELD_ALIASES.items():
        lookup[normalize(field)] = field
        for alias in aliases:
            lookup[normalize(alias)] = field
    return lookup


_NORMALIZED_LOOKUP = _build_lookup()


def match_field(header_text: str) -> str | None:
    """Return the internal field a template header maps to, or None if
    no known alias matches (the caller leaves that column blank and logs
    a warning - it never raises)."""
    return _NORMALIZED_LOOKUP.get(normalize(header_text))
