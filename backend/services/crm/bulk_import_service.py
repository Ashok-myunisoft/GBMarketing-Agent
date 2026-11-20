from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urljoin

import httpx

from agents.export_agent import ExportAgent
from core.config import settings
from schemas.crm_import import CRMImportRow
from services.excel_template.template_reader import read_template_layout
from services.job_store import JobStore

logger = logging.getLogger(__name__)

CRM_IMPORT_FILENAME = "LeadImport_template.xlsx"
_NONE = "None"
_CRM_FIELD_MAP = {
    "company_name": "lead_name", "industry": "description",
    "contact_person": "contact_name", "designation": "designation",
    "phone": "contact_mobile_no", "email": "contact_email",
    "address": "address1", "gst": "gst", "city": "city",
}
_CRM_STATIC_FIELD_MAP = {
    "Company": "company", "Stage": "stage", "Remarks": "remarks",
    "Incharge": "incharge", "Alloted To": "alloted_to",
    "Source Detail": "source_detail",
}


class CRMImportError(RuntimeError):
    """An error while preparing or sending a CRM Bulk Import request."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _text_or_none(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text else _NONE


def _ten_digit_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 10 else _NONE


def crm_row_for_company(company: dict[str, Any], source_index: int,
                        fixed_values: dict[str, str] | None = None) -> dict[str, Any]:
    """Map one Marketing job result to the CRM sheet columns."""
    return {
        **(fixed_values or {}),
        "source_index": source_index,
        "approved": True,
        "lead_name": _text_or_none(company.get("company_name")),
        "description": _text_or_none(company.get("industry")),
        "contact_name": _text_or_none(company.get("contact_person")),
        "designation": _text_or_none(company.get("designation")),
        "contact_mobile_no": _ten_digit_phone(company.get("phone")),
        "contact_email": _text_or_none(company.get("email")),
        "address1": _text_or_none(company.get("address")),
        "gst": _text_or_none(company.get("gst")),
        "city": _text_or_none(company.get("city")),
    }


class CRMImportService:
    def __init__(self, job_store: JobStore):
        self.job_store = job_store

    def preview_job(self, job_id: str) -> list[dict[str, Any]]:
        job = self.job_store.get(job_id)
        if not job:
            raise CRMImportError("Job not found.", 404)
        if job.get("status") != "completed":
            raise CRMImportError("The lead search must finish before CRM review.", 409)
        companies = (job.get("result") or {}).get("companies", [])
        fixed_values = {
            column["key"]: column["static_value"]
            for column in self.preview_columns()
            if column["key"] in _CRM_STATIC_FIELD_MAP.values()
        }
        fixed_values["incharge"] = job.get("incharge") or fixed_values.get("incharge", _NONE)
        fixed_values["alloted_to"] = job.get("alloted_to") or fixed_values.get("alloted_to", _NONE)
        return [crm_row_for_company(company, index, fixed_values) for index, company in enumerate(companies)]

    @staticmethod
    def preview_columns() -> list[dict[str, Any]]:
        """Expose the workbook's column order and fixed values to the review table."""
        layout = read_template_layout(
            ExportAgent._resolve_template_path(),
            header_row=settings.EXCEL_TEMPLATE_HEADER_ROW,
        )
        return [
            {
                "header": column.header or "",
                "key": _CRM_FIELD_MAP.get(column.field or "") or _CRM_STATIC_FIELD_MAP.get(column.header or ""),
                "static_value": column.static_value or "",
            }
            for column in layout.columns
        ]

    async def upload_job_rows(
        self, job_id: str, rows: list[CRMImportRow], login_dto: dict[str, Any]
    ) -> dict[str, Any]:
        job = self.job_store.get(job_id)
        if not job:
            raise CRMImportError("Job not found.", 404)
        if job.get("status") != "completed":
            raise CRMImportError("The lead search must finish before CRM upload.", 409)

        companies = (job.get("result") or {}).get("companies", [])
        seen_indexes: set[int] = set()
        approved_rows: list[CRMImportRow] = []
        for row in rows:
            if row.source_index >= len(companies):
                raise CRMImportError("A selected lead does not belong to this search job.", 400)
            if row.source_index in seen_indexes:
                raise CRMImportError("The upload contains the same search lead more than once.", 400)
            seen_indexes.add(row.source_index)
            if row.approved:
                approved_rows.append(self._normalize_row(row))

        if not approved_rows:
            raise CRMImportError("Select at least one lead to upload.", 400)

        workbook_bytes = self._build_workbook(approved_rows)
        result = await self._send_file(CRM_IMPORT_FILENAME, workbook_bytes, login_dto)
        return {
            "file_name": CRM_IMPORT_FILENAME,
            "uploaded_count": len(approved_rows),
            "crm_response": result["response"],
            "crm_status_code": result["status_code"],
        }

    async def upload_manual_file(
        self, file_name: str, file_bytes: bytes, login_dto: dict[str, Any]
    ) -> dict[str, Any]:
        safe_name = Path(file_name.replace("\\", "/")).name
        if not safe_name.lower().endswith(".xlsx"):
            raise CRMImportError("Choose an .xlsx file for CRM import.", 400)
        if not file_bytes:
            raise CRMImportError("The selected Excel file is empty.", 400)
        result = await self._send_file(safe_name, file_bytes, login_dto)
        return {
            "file_name": safe_name,
            "uploaded_count": None,
            "crm_response": result["response"],
            "crm_status_code": result["status_code"],
        }

    @staticmethod
    def _normalize_row(row: CRMImportRow) -> CRMImportRow:
        values = row.model_dump()
        for field in (
            "description", "contact_name", "designation", "contact_email",
            "address1", "gst", "city",
        ):
            values[field] = _text_or_none(values[field])
        values["contact_mobile_no"] = _ten_digit_phone(values["contact_mobile_no"])
        values["lead_name"] = str(values["lead_name"]).strip()
        return CRMImportRow(**values)

    @staticmethod
    def _build_workbook(rows: list[CRMImportRow]) -> bytes:
        from openpyxl import load_workbook
        from pathlib import Path

        with TemporaryDirectory(prefix="crm-import-") as temporary:
            output_path = Path(temporary) / CRM_IMPORT_FILENAME
            template_path = ExportAgent._resolve_template_path()
            layout = read_template_layout(
                template_path, header_row=settings.EXCEL_TEMPLATE_HEADER_ROW
            )
            workbook = load_workbook(template_path)
            sheet = workbook[layout.sheet_name]
            if sheet.max_row > layout.header_row:
                sheet.delete_rows(layout.header_row + 1, sheet.max_row - layout.header_row)
            for row_index, row in enumerate(rows, start=layout.header_row + 1):
                for column in layout.columns:
                    key = _CRM_FIELD_MAP.get(column.field or "") or _CRM_STATIC_FIELD_MAP.get(column.header or "")
                    if key:
                        value = getattr(row, key)
                    elif column.static_value is not None:
                        value = column.static_value
                    elif column.field == "extracted_on":
                        from datetime import datetime
                        value = datetime.now().strftime("%Y-%m-%d %H:%M")
                    else:
                        value = ""
                    sheet.cell(row=row_index, column=column.index, value=value)
            sheet.auto_filter.ref = sheet.dimensions
            workbook.save(output_path)
            return output_path.read_bytes()

    @staticmethod
    def _import_url_and_headers(login_dto: dict[str, Any]) -> tuple[str, dict[str, str]]:
        base_url = str(login_dto.get("BaseUri") or "").strip()
        if not base_url.startswith(("https://", "http://")):
            base_url = f"https://{base_url}"
        # GoodBooks may return its internal localhost address as BaseUri.
        # Use the configured public API host for that case.
        if base_url.startswith(("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1")):
            base_url = settings.ERP_LOGIN_URL
        url = urljoin(base_url.rstrip("/") + "/", settings.CRM_IMPORT_PATH.lstrip("/"))
        headers = {
            # The Bulk Import API binds the authenticated user's details as `Login`.
            "Login": json.dumps(login_dto, ensure_ascii=False, separators=(",", ":")),
        }
        return url, headers

    async def _send_file(self, file_name: str, file_bytes: bytes, login_dto: dict[str, Any]) -> dict[str, Any]:
        url, headers = self._import_url_and_headers(login_dto)
        payload = {
            "importFileType": settings.CRM_IMPORT_FILE_TYPE,
            "bulkIceId": settings.CRM_BULK_ICE_ID,
            "files": [{
                "fileName": file_name,
                "base64Content": base64.b64encode(file_bytes).decode("ascii"),
            }],
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.CRM_REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            logger.exception("CRM Bulk Import request could not reach the CRM endpoint")
            raise CRMImportError("Unable to reach the CRM Bulk Import endpoint.") from exc

        try:
            response_data: Any = response.json()
        except ValueError:
            response_data = {"response_text": response.text[:4000]}

        if response.is_error:
            logger.warning('GoodBooks import HTTP failure: status=%s file=%s', response.status_code, file_name)
            raise CRMImportError(
                f"CRM Bulk Import returned HTTP {response.status_code}."
            )
        if isinstance(response_data, dict) and response_data.get('Status') not in (None, 200):
            body = response_data.get('Body') or {}
            errors = body.get('GeneralErrors') if isinstance(body, dict) else None
            logger.warning('GoodBooks import rejected: status=%s errors=%s', response_data.get('Status'), errors)
            raise CRMImportError(f"GoodBooks rejected the import: {errors or 'Unknown CRM error'}")
        logger.info('GoodBooks import response: HTTP %s, CRM status %s, file %s', response.status_code, response_data.get('Status') if isinstance(response_data, dict) else 'unknown', file_name)
        return {"status_code": response.status_code, "response": response_data}
