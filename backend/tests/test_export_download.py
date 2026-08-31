"""Tests for GET /jobs/{job_id}/export.

Per the updated business requirement, the download endpoint exports the
COMPLETE current contents of PostgreSQL public.mlead - never just the
leads newly extracted by that particular job. job_id is used only to
verify the job exists and has completed. These tests use a
FakeMleadRepository (implementing only the read-only surface the
endpoint is allowed to use) so nothing here touches a real database.
"""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

import api.routers as routers_module
from main import app
from schemas.company import Company
from services.job_store import JobStore


PRODUCTION_TEMPLATE_HEADERS = [
    "Company", "Lead Name", "Description", "Contact Name", "Designation",
    "Contact Mobile No", "Contact Email", "Address1", "GST", "City",
    "Stage", "Remarks", "Incharge", "Alloted To", "Source Detail",
]


def _lead(i: int, **overrides) -> Company:
    base = dict(
        company_name=f"Company {i}",
        gst=f"27GST{i:05d}F1Z5",
        website=f"https://company{i}.example",
        phone=f"90000{i:05d}",
        email=f"lead{i}@example.com",
        city="Coimbatore",
    )
    base.update(overrides)
    return Company(**base)


class FakeMleadRepository:
    """Stands in for MleadRepository. Implements ONLY get_all_leads() -
    the read-only surface the download endpoint is allowed to use - plus
    a call counter. Any write attempt raises loudly instead of silently
    mutating a real database, which is how these tests prove the
    download path is read-only with respect to PostgreSQL."""

    def __init__(self, leads: list[Company]):
        self._leads = list(leads)
        self.read_calls = 0

    def get_all_leads(self) -> list[Company]:
        self.read_calls += 1
        return list(self._leads)

    def save(self, *args, **kwargs):
        raise AssertionError("Download must not write to public.mlead")


class ExportDownloadEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.job_store = JobStore(database_path=Path(self._tmp.name) / "jobs.sqlite3")
        self._original_job_store = routers_module.job_service.store
        self._original_mlead_repo = routers_module.mlead_repository
        routers_module.job_service.store = self.job_store
        self.addCleanup(self._restore)
        self.client = TestClient(app)

    def _restore(self):
        routers_module.job_service.store = self._original_job_store
        routers_module.mlead_repository = self._original_mlead_repo

    def _seed_completed_job(self, job_result_companies: list[dict]) -> str:
        job = self.job_store.create("valve manufacturers in Coimbatore")
        job_id = job["id"]
        self.job_store.start(job_id)
        self.job_store.complete(job_id, {"companies": job_result_companies, "export_path": None})
        return job_id

    def _use_fake_db(self, leads: list[Company]) -> FakeMleadRepository:
        fake = FakeMleadRepository(leads)
        routers_module.mlead_repository = fake
        return fake

    # TESTS 1 & 2: DB has 300 existing + 30 newly-persisted leads = 330.
    # The job's own result only reflects the 30 it newly extracted.
    # Download must return all 330.
    def test_download_returns_complete_db_dataset_not_job_subset(self):
        existing = [_lead(i) for i in range(300)]
        newly_extracted = [_lead(300 + i) for i in range(30)]
        fake = self._use_fake_db(existing + newly_extracted)  # DB after persistence: 330
        job_id = self._seed_completed_job([c.model_dump(mode="json") for c in newly_extracted])

        response = self.client.get(f"/jobs/{job_id}/export")
        self.assertEqual(response.status_code, 200)
        wb = load_workbook(filename=io.BytesIO(response.content))
        sheet = wb["LeadCommonImportDTO"]
        self.assertEqual(sheet.max_row - 1, 330)  # header + 330 data rows
        self.assertEqual(fake.read_calls, 1)

    # TEST 3: a completed job whose result["companies"] contains only 30
    # leads must still download ALL 330 DB leads.
    def test_job_companies_field_is_ignored_as_export_source(self):
        db_leads = [_lead(i) for i in range(330)]
        self._use_fake_db(db_leads)
        job_id = self._seed_completed_job([c.model_dump(mode="json") for c in db_leads[:30]])

        response = self.client.get(f"/jobs/{job_id}/export")
        wb = load_workbook(filename=io.BytesIO(response.content))
        sheet = wb["LeadCommonImportDTO"]
        self.assertEqual(sheet.max_row - 1, 330)

    # TEST 4: no previous Excel output file exists anywhere - the
    # tempfile-based generation never depends on one.
    def test_download_succeeds_with_no_preexisting_excel_file(self):
        self._use_fake_db([_lead(1)])
        job_id = self._seed_completed_job([])
        response = self.client.get(f"/jobs/{job_id}/export")
        self.assertEqual(response.status_code, 200)

    # TEST 5: download is read-only with respect to PostgreSQL.
    def test_download_never_writes_to_mlead(self):
        fake = self._use_fake_db([_lead(1), _lead(2)])
        job_id = self._seed_completed_job([])
        response = self.client.get(f"/jobs/{job_id}/export")
        self.assertEqual(response.status_code, 200)
        # FakeMleadRepository.save() would raise if ever called; reaching
        # here with the leads list unchanged proves no write happened.
        self.assertEqual(len(fake._leads), 2)
        self.assertEqual(fake.read_calls, 1)

    # TEST 6: two different jobs, both completed, with the DB having
    # grown between them - either job's download must reflect the
    # CURRENT complete DB dataset, not "its own" leads.
    def test_either_completed_job_returns_the_full_current_db_dataset(self):
        job_a_id = self._seed_completed_job([c.model_dump(mode="json") for c in [_lead(i) for i in range(30)]])
        self._use_fake_db([_lead(i) for i in range(350)])  # DB has grown by the time job B completes
        job_b_id = self._seed_completed_job(
            [c.model_dump(mode="json") for c in [_lead(i) for i in range(330, 350)]]
        )

        for job_id in (job_a_id, job_b_id):
            response = self.client.get(f"/jobs/{job_id}/export")
            wb = load_workbook(filename=io.BytesIO(response.content))
            sheet = wb["LeadCommonImportDTO"]
            self.assertEqual(sheet.max_row - 1, 350, f"job {job_id} should reflect the full current DB dataset")

    # TEST 7: columns exactly follow the current production template.
    def test_columns_follow_current_template_exactly(self):
        self._use_fake_db([_lead(1)])
        job_id = self._seed_completed_job([])
        response = self.client.get(f"/jobs/{job_id}/export")
        wb = load_workbook(filename=io.BytesIO(response.content))
        sheet = wb["LeadCommonImportDTO"]
        self.assertEqual([c.value for c in sheet[1]], PRODUCTION_TEMPLATE_HEADERS)

    # TEST 8: swapping the template requires no Python code changes - the
    # endpoint always uses whatever settings.EXCEL_TEMPLATE_PATH currently
    # points to, same as ExportAgent always has.
    def test_template_replacement_requires_no_code_change(self):
        self._use_fake_db([_lead(1)])
        job_id = self._seed_completed_job([])

        alt_template_dir = tempfile.TemporaryDirectory()
        self.addCleanup(alt_template_dir.cleanup)
        alt_template_path = Path(alt_template_dir.name) / "alt_template.xlsx"
        wb = Workbook()
        sheet = wb.active
        sheet.title = "Leads"
        sheet.append(["Company Name", "GST"])
        wb.save(alt_template_path)

        with patch("agents.export_agent.settings.EXCEL_TEMPLATE_PATH", str(alt_template_path)), \
             patch("agents.export_agent.settings.EXCEL_TEMPLATE_HEADER_ROW", None):
            response = self.client.get(f"/jobs/{job_id}/export")

        self.assertEqual(response.status_code, 200)
        out_wb = load_workbook(filename=io.BytesIO(response.content))
        self.assertEqual([c.value for c in out_wb["Leads"][1]], ["Company Name", "GST"])

    def test_job_not_found_returns_404(self):
        self._use_fake_db([])
        response = self.client.get("/jobs/does-not-exist/export")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Job not found")

    def test_incomplete_job_returns_export_not_available_yet(self):
        self._use_fake_db([])
        job = self.job_store.create("valve manufacturers in Coimbatore")
        self.job_store.start(job["id"])  # still "running", not completed
        response = self.client.get(f"/jobs/{job['id']}/export")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Export is not available yet")


if __name__ == "__main__":
    unittest.main()
