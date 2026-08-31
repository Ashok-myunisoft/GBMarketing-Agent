
from datetime import datetime
from pathlib import Path
from typing import Optional

from agents.base_agent import BaseClass
from core.config import settings
from schemas.company import Company
from services.excel_template.template_reader import TemplateLayout, read_template_layout

import logging

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
MASTER_EXPORT_PATH = BACKEND_DIR / "exports" / "All_Extracted_Leads.xlsx"


class ExportAgent(BaseClass):
    """Writes accepted leads to Excel using a TEMPLATE as the source of
    truth for output column order/headers/formatting.

    Nothing about the output schema is hardcoded here: the template file
    at settings.EXCEL_TEMPLATE_PATH defines every column, and
    services/excel_template resolves each header to an extracted Company
    field via a small, centralized alias registry. Swapping in a new
    business-provided template requires no changes to this file.
    """

    def execute(self, companies: list[Company], output_path: Optional[str] = None) -> str:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("Excel export requires openpyxl; install dependencies first.") from exc

        template_path = self._resolve_template_path()
        if not template_path.exists():
            raise RuntimeError(
                f"Excel template not found at {template_path}. Set EXCEL_TEMPLATE_PATH "
                "or place a template at backend/templates/lead_template.xlsx."
            )
        layout = read_template_layout(template_path, header_row=settings.EXCEL_TEMPLATE_HEADER_ROW)

        path = Path(output_path) if output_path else MASTER_EXPORT_PATH
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            workbook = load_workbook(path)
            sheet = workbook[layout.sheet_name] if layout.sheet_name in workbook.sheetnames else workbook.active
            existing_headers = [cell.value for cell in sheet[layout.header_row]]
            if existing_headers != layout.header_values:
                # The template's structure has changed since this output
                # file was created. Never silently drop old data or crash:
                # archive the previous output (same convention already used
                # by this project - see exports/*.backup_*.xlsx) and start a
                # fresh output built from the current template.
                backup_path = path.with_name(
                    f"{path.stem}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}"
                )
                logger.warning(
                    "Excel template structure changed; archiving previous output %s "
                    "to %s and starting a new output from %s",
                    path, backup_path, template_path,
                )
                try:
                    path.rename(backup_path)
                except OSError as exc:
                    # The only way to reach this branch is an OS-level file
                    # operation, not template/column logic - most commonly
                    # the existing export file being open in Excel or
                    # locked by another process (sync client, antivirus).
                    # Previously this OSError propagated with no logging at
                    # all, so a locked file failed the whole job silently.
                    raise RuntimeError(
                        f"Could not archive the previous export file at {path}: {exc}. "
                        "It may be open in Excel or locked by another program - "
                        "close it and retry."
                    ) from exc
                workbook, sheet = self._new_output_from_template(template_path, layout)
        else:
            workbook, sheet = self._new_output_from_template(template_path, layout)

        extracted_on = datetime.now().strftime("%Y-%m-%d %H:%M")
        start_row = sheet.max_row + 1
        for row_offset, company in enumerate(companies):
            for column in layout.columns:
                sheet.cell(
                    row=start_row + row_offset,
                    column=column.index,
                    value=self._resolve_value(column, company, extracted_on),
                )

        if not sheet.freeze_panes:
            sheet.freeze_panes = sheet.cell(row=layout.header_row + 1, column=1).coordinate
        sheet.auto_filter.ref = sheet.dimensions
        for column_cells in sheet.columns:
            letter = column_cells[0].column_letter
            widest = max((len(str(cell.value or "")) for cell in column_cells), default=0)
            current = sheet.column_dimensions[letter].width or 0
            sheet.column_dimensions[letter].width = min(max(widest + 2, current), 50)

        try:
            workbook.save(path)
        except OSError as exc:
            raise RuntimeError(
                f"Could not write the export file at {path}: {exc}. "
                "It may be open in Excel or locked by another program - "
                "close it and retry."
            ) from exc
        return str(path.resolve())

    @staticmethod
    def _resolve_template_path() -> Path:
        template_path = Path(settings.EXCEL_TEMPLATE_PATH)
        if not template_path.is_absolute():
            template_path = BACKEND_DIR / template_path
        return template_path

    @staticmethod
    def _new_output_from_template(template_path: Path, layout: TemplateLayout):
        """Start a new output workbook by opening the template itself, so
        header formatting/fonts/column widths/merged cells/frozen panes and
        the worksheet name are preserved rather than rebuilt from scratch.

        Some business-provided templates ship with an illustrative sample
        row under the header (e.g. a single example company name) purely
        for the person filling it in by hand. That row is part of the
        template file, not real data, so it is stripped from the OUTPUT
        copy before any real rows are appended - the template file on disk
        is never modified.
        """
        from openpyxl import load_workbook
        workbook = load_workbook(template_path)
        sheet = workbook[layout.sheet_name] if layout.sheet_name in workbook.sheetnames else workbook.active
        if sheet.max_row > layout.header_row:
            sheet.delete_rows(layout.header_row + 1, sheet.max_row - layout.header_row)
        return workbook, sheet

    @staticmethod
    def _resolve_value(column: "TemplateColumn", company: Company, extracted_on: str):
        if column.static_value is not None:
            return column.static_value
        field = column.field
        if field is None:
            return ""
        if field == "extracted_on":
            return extracted_on
        value = getattr(company, field, None)
        if value is None:
            return ""
        return "; ".join(value) if isinstance(value, list) else value
