"""Reads an Excel TEMPLATE file and turns it into a TemplateLayout.

This module is the other half of the "template is the source of truth"
design already documented in agents/export_agent.py:

- Column order, column headers, and the sheet name all come from
  whatever workbook is currently at settings.EXCEL_TEMPLATE_PATH -
  nothing about the output shape is hardcoded here.
- Each header is resolved to an extracted Company field, a fixed/static
  value, or "intentionally blank" using services/excel_template/
  template_mapping.py's optional hidden mapping sheet when the template
  has one, falling back to the older field_aliases.match_field() alias
  registry when it doesn't (see template_mapping.py's module docstring
  for the full contract - this keeps every template built before that
  mechanism existed working unchanged).
- Swapping in a structurally different business template - new columns,
  renamed columns, reordered columns, static values, columns dropped -
  requires no code change anywhere: agents/export_agent.py only ever
  calls read_template_layout(template_path, header_row=...) and reads
  the TemplateLayout it gets back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from services.excel_template.field_aliases import match_field
from services.excel_template.template_mapping import MAPPING_SHEET_NAME, read_mapping_overrides
from services.excel_template.field_aliases import normalize

import logging

logger = logging.getLogger(__name__)

# How many leading rows to inspect when the header row isn't pinned via
# settings.EXCEL_TEMPLATE_HEADER_ROW. Business templates seen so far put
# the header on row 1 with, at most, a single illustrative sample row
# beneath it - 10 rows is generous headroom without scanning the whole
# sheet.
_HEADER_SCAN_ROWS = 10


@dataclass(frozen=True)
class TemplateColumn:
    """One column of the template, in template order.

    index is the 1-based openpyxl column number (so it can be passed
    straight to sheet.cell(row=..., column=column.index)).

    Exactly one of these is meaningful for any given column:
    - field: a Company attribute name (or the virtual "extracted_on") -
      the caller should look this value up on the Company being exported.
    - static_value: a fixed string the template itself defines (e.g. a
      constant "Company" = "Goodbooks" column) - the caller should write
      this exact value for every exported row, regardless of company.

    When both are None, the column has no known mapping (an
    intentionally unmapped/marketing-owned column, or a template column
    the caller could not safely resolve) - callers must leave it blank
    rather than fabricate a value.
    """

    index: int
    header: Optional[str]
    field: Optional[str] = None
    static_value: Optional[str] = None


@dataclass(frozen=True)
class TemplateLayout:
    """The resolved shape of a template: which sheet, which row holds the
    headers, the header values themselves (used to detect that a template
    has changed - see agents/export_agent.py), and the ordered columns."""

    sheet_name: str
    header_row: int
    header_values: list
    columns: list  # list[TemplateColumn]


def _detect_header_row(sheet, scan_rows: int = _HEADER_SCAN_ROWS) -> int:
    """Pick the row most likely to be the header row: the one with the
    most non-empty text cells among the first `scan_rows` rows. Ties go
    to the earliest row. This correctly skips a single illustrative
    sample-data row placed under the real header (e.g. the "Goodbooks"
    row in the production template), since a sample row has far fewer
    populated cells than the real header row.
    """
    max_row = min(sheet.max_row or 1, scan_rows)
    best_row = 1
    best_count = -1
    for row_idx in range(1, max_row + 1):
        count = sum(
            1 for cell in sheet[row_idx]
            if isinstance(cell.value, str) and cell.value.strip()
        )
        if count > best_count:
            best_count = count
            best_row = row_idx
    return best_row


def _resolve_data_sheet(workbook, sheet_name: Optional[str]):
    """Pick the sheet that holds the actual export columns - never the
    hidden mapping-config sheet, even if a template author accidentally
    leaves it as the active sheet."""
    if sheet_name and sheet_name in workbook.sheetnames:
        return workbook[sheet_name]

    candidate = workbook.active
    if candidate is not None and candidate.title != MAPPING_SHEET_NAME:
        return candidate

    for title in workbook.sheetnames:
        if title != MAPPING_SHEET_NAME:
            return workbook[title]

    # A workbook containing only the mapping sheet is invalid, but return
    # what we have rather than crash with an unhelpful IndexError.
    return candidate


def read_template_layout(
    template_path: Union[str, Path],
    header_row: Optional[int] = None,
    sheet_name: Optional[str] = None,
) -> TemplateLayout:
    """Read the template at template_path and return its TemplateLayout.

    header_row: 1-based row number to treat as the header row. When None
    (the default, and what settings.EXCEL_TEMPLATE_HEADER_ROW is unless
    explicitly overridden), the header row is auto-detected.
    sheet_name: sheet to read. When None, the workbook's active sheet is
    used (skipping the hidden mapping sheet if present) - the same sheet
    a template author sees first when opening the file, and consistent
    with how agents/export_agent.py falls back to workbook.active when a
    saved output's sheet name doesn't match.
    """
    from openpyxl import load_workbook

    template_path = Path(template_path)
    workbook = load_workbook(template_path, data_only=True)
    try:
        sheet = _resolve_data_sheet(workbook, sheet_name)

        resolved_header_row = header_row if header_row is not None else _detect_header_row(sheet)

        header_cells = sheet[resolved_header_row]
        # sheet[row] returns a single Cell (not a tuple) when the sheet
        # has exactly one column - normalize to a tuple either way.
        if not isinstance(header_cells, tuple):
            header_cells = (header_cells,)

        header_values = [cell.value for cell in header_cells]

        # None when the template has no mapping sheet at all -> every
        # column falls back to alias matching, unchanged from before this
        # mechanism existed. A dict (even an empty one) means the mapping
        # sheet IS present and is authoritative for every column it lists;
        # a template column with no corresponding mapping-sheet row is
        # then treated as unmapped rather than silently falling back to
        # alias guessing - see template_mapping.py's docstring for why.
        overrides = read_mapping_overrides(workbook)

        columns = []
        for cell in header_cells:
            header_text = cell.value
            field: Optional[str] = None
            static_value: Optional[str] = None

            if header_text:
                if overrides is not None:
                    override = overrides.get(normalize(header_text))
                    if override is None:
                        logger.warning(
                            "Template column %r has no row in the %s mapping "
                            "sheet - leaving it blank rather than guessing.",
                            header_text, MAPPING_SHEET_NAME,
                        )
                    elif override.kind == "field":
                        field = override.field
                    elif override.kind == "static":
                        static_value = override.static_value
                    # "blank" and "invalid" both leave field/static_value
                    # as None (invalid already logged its own warning).
                else:
                    field = match_field(header_text)

            columns.append(
                TemplateColumn(
                    index=cell.column,
                    header=header_text,
                    field=field,
                    static_value=static_value,
                )
            )

        return TemplateLayout(
            sheet_name=sheet.title,
            header_row=resolved_header_row,
            header_values=header_values,
            columns=columns,
        )
    finally:
        workbook.close()
