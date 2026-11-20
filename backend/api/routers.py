import tempfile
import uuid
import logging
from pathlib import Path
from typing import Optional
import httpx

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import Response, RedirectResponse

from schemas.request import ChatRequest
from schemas.crm_import import CRMImportRowsRequest
from schemas.company import Company
from agents.conversation_agent import ConversationAgent
from agents.export_agent import ExportAgent
from core.config import settings
from services.llm_services import LLMTemporarilyUnavailableError
from services.job_service import JobService
from services.existing_data_service import ExistingDataService
from services.mautic.client import MauticClient, MauticError
# Mautic OAuth service
from services.mautic.oauth_service import MauticOAuthService
from services.mautic.mautic_dashboard import MauticDashboardService
from services.mautic.outreach_service import MauticOutreachService, MauticOutreachError
from services.crm.bulk_import_service import CRMImportError, CRMImportService
from services.crm.auth_service import authenticate, login_for_session, logout
from services.mlead.repository import MleadRepository


router = APIRouter()
logger = logging.getLogger(__name__)


class CRMLoginRequest(BaseModel):
    user_code: str
    password: str
    connection_name: str


class CreateJobRequest(ChatRequest):
    incharge: str = Field(default="", max_length=300)
    alloted_to: str = Field(default="", max_length=300)


@router.post('/crm/login')
async def crm_login(request: CRMLoginRequest):
    try:
        return await authenticate(request.user_code, request.password, request.connection_name)
    except CRMImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post('/crm/logout')
def crm_logout(x_crm_session: Optional[str] = Header(default=None)):
    logout(x_crm_session)
    return {'ok': True}

COMPANY_HOURLY_STATS_URL = "http://217.217.249.121:8041/stats/companies/hourly"

job_service = JobService()
existing_data_service = ExistingDataService()
mlead_repository = MleadRepository()


@router.get('/stats/leads/saved')
def get_saved_lead_count():
    try:
        return {'count': mlead_repository.count_leads()}
    except Exception as exc:
        logger.exception('Unable to count persisted leads')
        raise HTTPException(status_code=503, detail='Unable to load saved lead count.') from exc


@router.get("/stats/companies/hourly")
async def get_hourly_company_stats():
    """Proxy hourly company statistics for the dashboard."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(COMPANY_HOURLY_STATS_URL)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Hourly company stats service returned HTTP {exc.response.status_code}.") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Unable to load hourly company stats.") from exc

# Mautic OAuth service
mautic_oauth_service = MauticOAuthService()
mautic_client = MauticClient()
mautic_dashboard_service = MauticDashboardService(mautic_client)
mautic_outreach_service = MauticOutreachService(mautic_client, job_service.store)
crm_import_service = CRMImportService(job_service.store)

# ============================================================
# EXISTING MARKETING AGENT ROUTES
# ============================================================

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
def create_job(request: CreateJobRequest):
    """Starts the existing workflow in the background for the React UI."""
    return job_service.create(request.message, request.incharge.strip(), request.alloted_to.strip())


@router.get("/jobs")
def list_jobs(limit: int = Query(default=50, ge=1, le=200)):
    return job_service.store.list_jobs(limit)


@router.get("/stats/jobs")
def get_job_counts():
    return job_service.store.job_counts()


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = job_service.store.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return job


@router.get("/jobs/{job_id}/events")
def get_job_events(job_id: str):

    if not job_service.store.get(job_id):
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

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
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    indexed_companies = list(
        enumerate(
            (job.get("result") or {}).get("companies", [])
        )
    )

    if validation_status:
        indexed_companies = [
            (index, company)
            for index, company in indexed_companies
            if company.get("validation_status") == validation_status
        ]

    if missing_gst:
        indexed_companies = [
            (index, company)
            for index, company in indexed_companies
            if not company.get("gst")
        ]

    if missing_contact:
        indexed_companies = [
            (index, company)
            for index, company in indexed_companies
            if not company.get("contact_person")
            or not company.get("designation")
        ]

    total = len(indexed_companies)

    items = []

    for index, company in indexed_companies[offset:offset + limit]:
        items.append(
            {
                "id": f"{job_id}:{index}",
                **company
            }
        )

    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit
    }


@router.get("/leads/{lead_id}")
def get_lead(lead_id: str):

    try:
        job_id, index_text = lead_id.rsplit(":", 1)
        index = int(index_text)

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid lead ID"
        ) from exc

    job = job_service.store.get(job_id)

    companies = (
        (job or {})
        .get("result", {})
        .get("companies", [])
    )

    if not job or index < 0 or index >= len(companies):
        raise HTTPException(
            status_code=404,
            detail="Lead not found"
        )

    return {
        "id": lead_id,
        **companies[index]
    }


@router.get("/jobs/{job_id}/export")
def download_export(job_id: str):
    """Downloads only the leads produced by this completed search job."""

    job = job_service.store.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    if job.get("status") != "completed":
        raise HTTPException(
            status_code=404,
            detail="Export is not available yet"
        )

    companies = [
        Company.model_validate(company)
        for company in (job.get("result") or {}).get("companies", [])
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:

        output_path = Path(tmp_dir) / f"leads_{job_id}.xlsx"

        ExportAgent().execute(
            companies,
            output_path=str(output_path),
            column_overrides={
                "Incharge": job.get("incharge") or "None",
                "Alloted To": job.get("alloted_to") or "None",
            },
        )

        content = output_path.read_bytes()

    return Response(
        content=content,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition":
                f'attachment; filename="leads_{job_id}.xlsx"'
        },
    )


@router.get("/jobs/{job_id}/crm-import-preview")
def crm_import_preview(job_id: str):
    try:
        return {
            "rows": crm_import_service.preview_job(job_id),
            "columns": crm_import_service.preview_columns(),
        }
    except CRMImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/crm-upload")
async def upload_job_to_crm(job_id: str, request: CRMImportRowsRequest, x_crm_session: Optional[str] = Header(default=None)):
    try:
        login_dto = login_for_session(x_crm_session)
    except CRMImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    upload_id = uuid.uuid4().hex
    approved_count = sum(1 for row in request.rows if row.approved)
    job_service.store.begin_crm_upload(
        upload_id, job_id=job_id, source_type="search_job",
        file_name="LeadImport_template.xlsx", item_count=approved_count,
    )
    try:
        result = await crm_import_service.upload_job_rows(job_id, request.rows, login_dto)
        job_service.store.finish_crm_upload(upload_id, status="completed", response=result)
        return {"upload_id": upload_id, **result}
    except CRMImportError as exc:
        logger.warning('CRM upload failed upload_id=%s job_id=%s reason=%s', upload_id, job_id, exc)
        job_service.store.finish_crm_upload(upload_id, status="failed", error=str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('CRM upload failed unexpectedly upload_id=%s job_id=%s', upload_id, job_id)
        job_service.store.finish_crm_upload(upload_id, status="failed", error="CRM upload failed.")
        raise HTTPException(status_code=500, detail="CRM upload failed.") from exc


@router.post("/crm/upload-file")
async def upload_manual_file_to_crm(file: UploadFile = File(...), x_crm_session: Optional[str] = Header(default=None)):
    try:
        login_dto = login_for_session(x_crm_session)
    except CRMImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    file_name = Path(file.filename or "upload.xlsx").name
    upload_id = uuid.uuid4().hex
    job_service.store.begin_crm_upload(
        upload_id, job_id=None, source_type="manual_file", file_name=file_name,
        item_count=0,
    )
    try:
        content = await file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise CRMImportError("Excel uploads must be 20 MB or smaller.", 413)
        result = await crm_import_service.upload_manual_file(file_name, content, login_dto)
        job_service.store.finish_crm_upload(upload_id, status="completed", response=result)
        return {"upload_id": upload_id, **result}
    except CRMImportError as exc:
        logger.warning('CRM manual upload failed upload_id=%s reason=%s', upload_id, exc)
        job_service.store.finish_crm_upload(upload_id, status="failed", error=str(exc))
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception('CRM manual upload failed unexpectedly upload_id=%s', upload_id)
        job_service.store.finish_crm_upload(upload_id, status="failed", error="CRM upload failed.")
        raise HTTPException(status_code=500, detail="CRM upload failed.") from exc
    finally:
        await file.close()


@router.get("/crm/uploads")
def crm_upload_history(
    job_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    return job_service.store.crm_upload_history(job_id=job_id, limit=limit)


@router.get("/existing-data")
def list_existing_data():
    """Show the files that ValidationAgent already uses for deduplication."""

    return existing_data_service.list_files()


@router.post("/existing-data", status_code=201)
async def upload_existing_data(
    file: UploadFile = File(...)
):
    """Add a deduplication baseline file without altering workflow behaviour."""

    return await existing_data_service.upload(file)


@router.delete("/existing-data/{filename}", status_code=204)
def delete_existing_data(filename: str):

    existing_data_service.delete(filename)


# ============================================================
# MAUTIC OAUTH 2 INTEGRATION
# ============================================================

@router.get("/mautic/connect")
async def mautic_connect():
    """
    Start the Mautic OAuth2 authorization flow.

    User opens:

        /mautic/connect

    Marketing Agent generates the authorization URL and
    redirects the user to Mautic.
    """

    try:
        authorization_url = (
            await mautic_oauth_service.get_authorization_url()
        )

        return RedirectResponse(
            url=authorization_url,
            status_code=302
        )

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Unable to start Mautic OAuth: {str(exc)}"
        ) from exc


@router.get("/mautic/callback")
async def mautic_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """
    OAuth2 callback endpoint.

    Mautic redirects the browser here after authorization.

    Expected:

        /mautic/callback?code=XXXXX&state=XXXXX
    """

    # --------------------------------------------------------
    # Mautic returned an OAuth error
    # --------------------------------------------------------

    if error:

        raise HTTPException(
            status_code=400,
            detail=f"Mautic OAuth authorization failed: {error}"
        )

    # --------------------------------------------------------
    # Authorization code validation
    # --------------------------------------------------------

    if not code:

        raise HTTPException(
            status_code=400,
            detail="Authorization code missing from Mautic callback"
        )

    # --------------------------------------------------------
    # OAuth state validation
    # --------------------------------------------------------

    if not state:

        raise HTTPException(
            status_code=400,
            detail="OAuth state missing from Mautic callback"
        )

    # --------------------------------------------------------
    # Exchange authorization code for token
    # --------------------------------------------------------

    try:

        result = await mautic_oauth_service.handle_callback(
            code=code,
            state=state
        )

        return result

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Mautic OAuth callback failed: {str(exc)}"
        ) from exc


@router.get("/mautic/status")
async def mautic_status():
    """
    Check whether the Marketing Agent is connected to Mautic.
    """

    try:

        result = await mautic_oauth_service.get_status()

        return result

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Unable to check Mautic status: {str(exc)}"
        ) from exc


@router.post("/mautic/disconnect")
async def mautic_disconnect():
    """
    Remove the stored Mautic OAuth connection.
    """

    try:

        result = await mautic_oauth_service.disconnect()

        return result

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Unable to disconnect Mautic: {str(exc)}"
        ) from exc


@router.get("/mautic/test")
async def mautic_test():
    try:
        return await mautic_client.health()

    except MauticError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

@router.get("/mautic/dashboard")
async def get_mautic_dashboard(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    search: Optional[str] = None,
):
    try:
        return await mautic_dashboard_service.get_dashboard(
            page=page,
            limit=limit,
            search=search,
        )

    except MauticError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to load Mautic dashboard data.",
        ) from exc


@router.get("/mautic/contacts/{contact_id}/activity")
async def get_mautic_contact_activity(contact_id: int):
    try:
        return await mautic_dashboard_service.get_contact_activity(
            contact_id
        )

    except MauticError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to load contact activity.",
        ) from exc


@router.post("/jobs/{job_id}/mautic-sync")
async def sync_job_leads_to_mautic(job_id: str):
    """
    Push this job's validated, emailed leads into Mautic and add them
    to the configured outreach campaign so Mautic sends the campaign
    email automatically.
    """

    try:
        return await mautic_outreach_service.sync_job_leads(
            job_id,
            campaign_id=settings.MAUTIC_OUTREACH_CAMPAIGN_ID,
        )

    except MauticOutreachError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except MauticError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Unable to sync job leads to Mautic.",
        ) from exc
    
