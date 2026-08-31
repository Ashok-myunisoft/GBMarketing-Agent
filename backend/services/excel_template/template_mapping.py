"""Reads an OPTIONAL hidden mapping sheet embedded in an Excel template.

This is the mechanism that lets a template fully describe its own column
semantics - which template column maps to which Company field, which
columns hold a fixed/static value, and which are intentionally left for
Marketing to fill in by hand - so that replacing the template file is
enough on its own; nothing in this package needs to change to support a
new template shape.

Sheet contract (MAPPING_SHEET_NAME, hidden, four columns):

    Template Column | Maps To Field | Static Value | Notes
    -----------------+---------------+--------------+------
    Company           | static        | Goodbooks    | fixed OU value
    Lead Name         | company_name  |              |
    Description       |               |              | marketing-owned
    ...

- "Template Column" is matched against the main sheet's header text using
  the SAME normalization field_aliases.py already uses (case/space/
  punctuation-insensitive), so small text edits don't break the mapping.
- "Maps To Field" is either:
    - the literal word "static" (case-insensitive)     -> use Static Value
    - a real Company field name (validated against the  -> map to that field
      actual Company model, never a hardcoded list, so
      this stays correct if Company gains/loses fields)
    - "extracted_on" - the one non-Company virtual field
      ExportAgent already understands (today's export date)
    - blank                                             -> intentionally
                                                            unmapped/blank
- Anything else in "Maps To Field" (a typo, a field that doesn't exist) is
  invalid: the caller must leave that column blank and log a warning
  rather than guess or crash.

If a workbook has NO sheet named MAPPING_SHEET_NAME at all, this module
returns None, and template_reader.py falls back to the pre-existing
field_aliases.match_field() alias matching unchanged - so templates that
never adopt a mapping sheet keep working exactly as before.

If a mapping sheet IS present, it is authoritative for every column it
lists. A column that appears in the main sheet's header row but has no
row in the mapping sheet is deliberately NOT passed through to alias
matching - mixing "explicit config for some columns, guessed aliases for
others" on the same template is exactly the kind of ambiguity the caller
must avoid, since the whole point of adding a mapping sheet is that a
header like "Company" might mean something template-specific (a fixed
value) rather than whatever field_aliases.py would otherwise guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from services.excel_template.field_aliases import normalize

import logging

logger = logging.getLogger(__name__)

MAPPING_SHEET_NAME = "__lead_export_mapping__"

_HEADER_COLUMN = "Template Column"
_FIELD_COLUMN = "Maps To Field"
_STATIC_VALUE_COLUMN = "Static Value"

_STATIC_KEYWORD = "static"

MappingKind = Literal["field", "static", "blank", "invalid"]


@dataclass(frozen=True)
class ColumnMappingOverride:
    """One row of the mapping sheet, already validated.

    kind == "field"   -> use `field` (a real Company attribute, or the
                          virtual "extracted_on") the same way alias
                          matching would.
    kind == "static"  -> always write `static_value` for this column,
                          regardless of the Company being exported.
    kind == "blank"   -> intentionally unmapped (e.g. Marketing-owned);
                          always leave the column blank.
    kind == "invalid" -> "Maps To Field" referenced something that isn't
                          a real Company field and isn't "static"/blank -
                          the caller must treat this exactly like "blank"
                          (leave the column blank) but a warning has
                          already been logged so the mistake is visible.
    """

    kind: MappingKind
    field: Optional[str] = None
    static_value: Optional[str] = None


def _known_company_fields() -> set[str]:
    """Real Company attribute names, pulled from the model itself so this
    never drifts out of sync with schemas/company.py, plus the one
    virtual field ExportAgent already understands (today's export date)."""
    from schemas.company import Company

    return set(Company.model_fields.keys()) | {"extracted_on"}


def _find_mapping_sheet(workbook):
    return workbook[MAPPING_SHEET_NAME] if MAPPING_SHEET_NAME in workbook.sheetnames else None


def read_mapping_overrides(workbook) -> Optional[dict[str, ColumnMappingOverride]]:
    """Return {normalized_header: ColumnMappingOverride, ...} for every row
    in the workbook's mapping sheet, or None if the workbook has no such
    sheet (legacy template - caller should fall back to alias matching).
    """
    sheet = _find_mapping_sheet(workbook)
    if sheet is None:
        return None

    header_cells = sheet[1]
    if not isinstance(header_cells, tuple):
        header_cells = (header_cells,)
    column_index = {cell.value: cell.column - 1 for cell in header_cells if cell.value}

    missing = {_HEADER_COLUMN, _FIELD_COLUMN} - set(column_index)
    if missing:
        logger.warning(
            "%s sheet is missing required column(s) %s; ignoring the mapping "
            "sheet entirely and falling back to header-alias matching.",
            MAPPING_SHEET_NAME, sorted(missing),
        )
        return None

    known_fields = _known_company_fields()
    overrides: dict[str, ColumnMappingOverride] = {}

    for row in sheet.iter_rows(min_row=2, values_only=True):
        if row is None or all(value in (None, "") for value in row):
            continue

        def cell(col_key: str) -> Optional[str]:
            idx = column_index.get(col_key)
            if idx is None or idx >= len(row):
                return None
            value = row[idx]
            return str(value).strip() if value is not None else None

        header_text = cell(_HEADER_COLUMN)
        if not header_text:
            continue
        key = normalize(header_text)

        field_text = cell(_FIELD_COLUMN)
        static_value = cell(_STATIC_VALUE_COLUMN)

        if not field_text:
            overrides[key] = ColumnMappingOverride(kind="blank")
        elif field_text.strip().lower() == _STATIC_KEYWORD:
            overrides[key] = ColumnMappingOverride(kind="static", static_value=static_value or "")
        elif field_text in known_fields:
            overrides[key] = ColumnMappingOverride(kind="field", field=field_text)
        else:
            logger.warning(
                "%s sheet: template column %r declares Maps To Field=%r, which "
                "is not a real Company field and not %r - leaving this column "
                "blank rather than guessing. Valid values are: %s, or %r.",
                MAPPING_SHEET_NAME, header_text, field_text, _STATIC_KEYWORD,
                sorted(known_fields), _STATIC_KEYWORD,
            )
            overrides[key] = ColumnMappingOverride(kind="invalid")

    return overrides
