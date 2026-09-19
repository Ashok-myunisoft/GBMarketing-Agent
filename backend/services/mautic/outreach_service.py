from __future__ import annotations

from typing import Any

from services.job_store import JobStore
from services.mautic.client import MauticClient, MauticError


class MauticOutreachError(RuntimeError):
    """Raised when a job cannot be synced to Mautic at all."""


class MauticOutreachService:
    """
    Pushes validated leads from a completed lead-generation job into
    Mautic and adds them to an outreach campaign, so Mautic sends the
    campaign's configured email automatically.

    This never touches TLead/MleadRepository - it only reads the
    in-memory job result already produced by the existing pipeline.
    """

    def __init__(self, client: MauticClient, job_store: JobStore):
        self.client = client
        self.job_store = job_store

    # ============================================================
    # SYNC
    # ============================================================

    async def sync_job_leads(
        self,
        job_id: str,
        campaign_id: int,
    ) -> dict[str, Any]:

        job = self.job_store.get(job_id)

        if not job:
            raise MauticOutreachError("Job not found.")

        if job.get("status") != "completed":
            raise MauticOutreachError(
                "Job is not completed yet."
            )

        companies = (
            (job.get("result") or {}).get("companies", [])
        )

        sent = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, Any]] = []

        # Sequential on purpose - concurrent Mautic calls can race on
        # the shared OAuth token refresh and invalidate it (see
        # MauticClient._get_access_token).
        for company in companies:

            email = (company.get("email") or "").strip()
            validation_status = company.get("validation_status")

            if not email or validation_status != "validated":
                skipped += 1
                continue

            try:
                contact_id = await self._get_or_create_contact(
                    email,
                    company,
                )

                await self.client.add_contact_to_campaign(
                    contact_id,
                    campaign_id,
                )

                sent += 1

            except MauticError as exc:

                failed += 1

                errors.append({
                    "company": company.get("company_name"),
                    "email": email,
                    "error": str(exc),
                })

        return {
            "job_id": job_id,
            "campaign_id": campaign_id,
            "sent": sent,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
        }

    # ============================================================
    # CONTACT LOOKUP / CREATE
    # ============================================================

    async def _get_or_create_contact(
        self,
        email: str,
        company: dict[str, Any],
    ) -> int:
        """
        Reuse an existing Mautic contact for this email if one
        already exists, otherwise create a new one. Avoids creating
        duplicate contacts when a job is synced more than once.
        """

        existing = await self.client.list_contacts(
            search=f"email:{email}",
            limit=1,
            page=1,
        )

        existing_contacts = existing.get("contacts", {})

        if isinstance(existing_contacts, dict) and existing_contacts:
            existing_id = next(iter(existing_contacts))
            return int(existing_id)

        contact_person = (
            company.get("contact_person") or ""
        ).strip()

        firstname, _, lastname = contact_person.partition(" ")

        payload = {
            "email": email,
            "firstname": firstname or None,
            "lastname": lastname or None,
            "company": company.get("company_name"),
            "phone": company.get("phone"),
            "city": company.get("city"),
            "state": company.get("state"),
            "website": company.get("website"),
            "position": company.get("designation"),
        }

        payload = {
            key: value
            for key, value in payload.items()
            if value
        }

        created = await self.client.create_contact(payload)

        contact_id = (created.get("contact") or {}).get("id")

        if not contact_id:
            raise MauticError(
                f"Mautic did not return a contact id for {email}."
            )

        return int(contact_id)
