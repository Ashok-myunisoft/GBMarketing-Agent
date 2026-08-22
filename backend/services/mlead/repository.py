"""PostgreSQL persistence for lead deduplication.

Backed by the EXISTING production table ``public.mlead`` in the ``cms``
database - not a new table. ``mlead`` already holds ~136 historical leads
imported from the old Excel baseline; this module never creates, drops,
truncates, or otherwise alters that table's structure, and never deletes
its rows.

This repository owns no matching/normalization logic of its own.
``agents.validation_agent.ValidationAgent._company_keys`` (untouched)
remains the single source of truth for what counts as a duplicate - this
module only converts ``mlead`` rows into the same raw fields the existing
``Company`` object already carries, and writes newly accepted leads back
using the field mapping below.

Field mapping (public.mlead -> Company):
    company_name             -> company_name
    gst                      -> gst
    website_url               -> website
    mobile_number              -> phone
    alternate_mobile_number    -> phone_alt
    email_id                    -> email
    city                      -> city
    industry_type              -> industry
    region                    -> region
    contact_person              -> contact_person
    designation                -> designation
    remarks                  -> remarks

``turn_over`` and ``linkedin_id`` are intentionally left untouched on
insert (no source field was specified for them) - never populated with
fabricated data.
"""

from __future__ import annotations

import logging
from contextlib import closing
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from core.config import settings

logger = logging.getLogger(__name__)


class MleadRepository:
    """Minimal read/write access to the existing ``public.mlead`` table."""

    def __init__(self, dsn: Optional[dict[str, Any] | str] = None):
        # DATABASE_URL (a full connection string) takes priority when set;
        # otherwise fall back to the separate POSTGRES_* variables, so an
        # explicit dict/string passed by a caller (e.g. tests) still works
        # exactly as before.
        if dsn is not None:
            self._dsn = dsn
        elif settings.DATABASE_URL:
            self._dsn = settings.DATABASE_URL
        else:
            self._dsn = {
                "host": settings.POSTGRES_HOST,
                "port": settings.POSTGRES_PORT,
                "dbname": settings.POSTGRES_DB,
                "user": settings.POSTGRES_USER,
                "password": settings.POSTGRES_PASSWORD,
            }

    def _connect(self):
        if isinstance(self._dsn, str):
            return psycopg2.connect(self._dsn)
        return psycopg2.connect(**self._dsn)

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------
    def fetch_all(self) -> list[dict[str, Any]]:
        """Return every existing lead's fields relevant to dedup/mapping.

        Callers reconstruct ``Company`` objects from these fields and run
        them through the existing ``_company_keys`` logic themselves -
        this method never decides what counts as a duplicate.
        """
        with closing(self._connect()) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT lead_id, company_name, gst, website_url,
                           mobile_number, alternate_mobile_number, email_id,
                           city, industry_type, region, contact_person,
                           designation
                    FROM public.mlead;
                    """
                )
                return [dict(row) for row in cur.fetchall()]

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------
    def save(
        self,
        *,
        company_name: str,
        gst: Optional[str] = None,
        website_url: Optional[str] = None,
        mobile_number: Optional[str] = None,
        alternate_mobile_number: Optional[str] = None,
        email_id: Optional[str] = None,
        city: Optional[str] = None,
        industry_type: Optional[str] = None,
        region: Optional[str] = None,
        contact_person: Optional[str] = None,
        designation: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> int:
        """Insert a newly accepted lead into public.mlead. Returns lead_id.

        Whether a lead is "new" is decided by the caller (by matching
        against ``fetch_all()`` output via the existing dedup keys) -
        this method performs the insert unconditionally, and only ever
        adds a row; it never updates or removes the 136 pre-existing
        historical rows.
        """
        with closing(self._connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.mlead
                        (company_name, gst, website_url, mobile_number,
                         alternate_mobile_number, email_id, city,
                         industry_type, region, contact_person, designation,
                         remarks)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING lead_id;
                    """,
                    (
                        company_name,
                        gst,
                        website_url,
                        mobile_number,
                        alternate_mobile_number,
                        email_id,
                        city,
                        industry_type,
                        region,
                        contact_person,
                        designation,
                        remarks,
                    ),
                )
                new_id = cur.fetchone()[0]
        logger.info("mlead: stored new lead lead_id=%s company=%s", new_id, company_name)
        return new_id