import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from schemas.request import ChatRequest
from agents.conversation_agent import ConversationAgent
from agents.export_agent import ExportAgent
from services.llm_services import LLMTemporarilyUnavailableError
from services.job_service import JobService
from services.existing_data_service import ExistingDataService
from services.mlead.repository import MleadRepository

router = APIRouter()
job_service = JobService()
existing_data_service = ExistingDataService()
mlead_repository = MleadRepository()

@router.post("/Ask")
def chat(request: ChatRequest):

    agent = ConversationAgent()

    try:
        response = agent.execute(request.message)
    except LLMTemporarilyUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return response


@router.post("/jobs", status_code=202)
def create_job(request: ChatRequest):
    """Starts the existing workflow in the background for the React UI."""
    return job_service.create(request.message)


@router.get("/jobs")
def list_jobs(limit: int = Query(default=50, ge=1, le=200)):
    return job_service.store.list_jobs(limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = job_service.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/jobs/{job_id}/events")
def get_job_events(job_id: str):
    if not job_service.store.get(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return job_service.store.events(job_id)


@router.get("/jobs/{job_id}/leads")
def get_job_leads(
    job_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    validation_status: Optional[str] = None,
    missing_gst: bool = False,
    missing_contact: bool = False,
):
    job = job_service.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    indexed_companies = list(enumerate((job.get("result") or {}).get("companies", [])))
    if validation_status:
        indexed_companies = [
            (index, company) for index, company in indexed_companies
            if company.get("validation_status") == validation_status
        ]
    if missing_gst:
        indexed_companies = [(index, company) for index, company in indexed_companies if not company.get("gst")]
    if missing_contact:
        indexed_companies = [
            (index, company) for index, company in indexed_companies
            if not company.get("contact_person") or not company.get("designation")
        ]
    total = len(indexed_companies)
    items = []
    for index, company in indexed_companies[offset:offset + limit]:
        items.append({"id": f"{job_id}:{index}", **company})
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: str):
    try:
        job_id, index_text = lead_id.rsplit(":", 1)
        index = int(index_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid lead ID") from exc
    job = job_service.store.get(job_id)
    companies = (job or {}).get("result", {}).get("companies", [])
    if not job or index < 0 or index >= len(companies):
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"id": lead_id, **companies[index]}


@router.get("/jobs/{job_id}/export")
def download_export(job_id: str):
    """Downloads the COMPLETE current public."TLead" dataset as XLSX.

    job_id is used ONLY to verify the requested job exists and has
    completed - per the updated business requirement, the exported
    dataset is always ALL of public."TLead" (the permanent source of
    truth), never just this job's newly extracted leads. See
    MleadRepository.get_all_leads().

    Read-only with respect to PostgreSQL: this issues a single SELECT
    (via MleadRepository) and nothing else - no insert/update/delete/
    deduplication happens as part of a download, and it never depends on
    job["result"]["export_path"] or job["result"]["companies"].
    """
    job = job_service.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Export is not available yet")

    try:
        companies = mlead_repository.get_all_leads()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Unable to read leads from the database") from exc

    with tempfile.TemporaryDirectory() as tmp_dir:
        # An explicit, not-yet-existing output path so ExportAgent builds a
        # fresh workbook - from the current lead_template.xlsx - rather
        # than appending to the shared cross-job master export file.
        output_path = Path(tmp_dir) / f"leads_{job_id}.xlsx"
        ExportAgent().execute(companies, output_path=str(output_path))
        content = output_path.read_bytes()

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="leads_{job_id}.xlsx"'},
    )


@router.get("/existing-data")
def list_existing_data():
    """Show the files that ValidationAgent already uses for deduplication."""
    return existing_data_service.list_files()


@router.post("/existing-data", status_code=201)
async def upload_existing_data(file: UploadFile = File(...)):
    """Add a deduplication baseline file without altering workflow behaviour."""
    return await existing_data_service.upload(file)


@router.delete("/existing-data/{filename}", status_code=204)
def delete_existing_data(filename: str):
    existing_data_service.delete(filename)
