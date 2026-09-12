"""PostgreSQL persistence for lead deduplication.

Backed by the EXISTING production table ``public."TLead"`` in the ``cms``
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

Field mapping (public."TLead" -> Company):
    company_name             -> company_name
    gst                      -> gst
    turn_over                 -> turnover
    website_url               -> website
    mobile_number              -> phone
    alternate_mobile_number    -> phone_alt
    email_id                    -> email
    city                      -> city
    address                  -> address
    industry_type              -> industry
    region                    -> region
    contact_person              -> contact_person
    designation                -> designation
    linkedin_id                -> linkedin_url
    remarks                  -> remarks
"""

from __future__ import annotations

import logging
from contextlib import closing
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from core.config import settings
from schemas.company import Company

logger = logging.getLogger(__name__)


class MleadRepository:
    """Minimal read/write access to the existing ``public."TLead"`` table."""

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
                    FROM public."TLead";
                    """
                )
                return [dict(row) for row in cur.fetchall()]

    def get_all_leads(self) -> list[Company]:
        """Return the COMPLETE current public."TLead" dataset as Company
        objects - every stored lead, not any particular job's newly
        extracted subset.

        This is the read-only counterpart to fetch_all() (which returns
        raw dicts, purpose-built for dedup-key matching). get_all_leads()
        is for the Download XLSX flow: PostgreSQL is the export dataset,
        so this issues a single SELECT and nothing else - no insert,
        update, delete, or deduplication happens here.
        """
        with closing(self._connect()) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT lead_id, company_name, gst, turn_over, region, city,
                           address, industry_type, contact_person, designation,
                           mobile_number, alternate_mobile_number, email_id,
                           linkedin_id, website_url, remarks
                    FROM public."TLead"
                    ORDER BY lead_id;
                    """
                )
                rows = cur.fetchall()
        return [self._row_to_company(row) for row in rows]

    @staticmethod
    def _row_to_company(row: dict[str, Any]) -> Company:
        """Map one public."TLead" row to a Company, handling NULLs safely."""
        turn_over = row.get("turn_over")
        return Company(
            company_name=row.get("company_name") or "",
            gst=row.get("gst"),
            turnover=str(turn_over) if turn_over is not None else None,
            region=row.get("region"),
            city=row.get("city"),
            address=row.get("address"),
            industry=row.get("industry_type"),
            contact_person=row.get("contact_person"),
            designation=row.get("designation"),
            phone=row.get("mobile_number"),
            phone_alt=row.get("alternate_mobile_number"),
            email=row.get("email_id"),
            linkedin_url=row.get("linkedin_id"),
            website=row.get("website_url"),
            remarks=row.get("remarks"),
        )

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------
    def save(
        self,
        *,
        company_name: str,
        gst: Optional[str] = None,
        # NUMERIC in the database - always a Crore figure (see
        # config/targeting.py's turnover_to_crore()), never the raw
        # "50 Crore"-style text label this pipeline extracts elsewhere.
        turnover: Optional[float] = None,
        website_url: Optional[str] = None,
        mobile_number: Optional[str] = None,
        alternate_mobile_number: Optional[str] = None,
        email_id: Optional[str] = None,
        city: Optional[str] = None,
        address: Optional[str] = None,
        industry_type: Optional[str] = None,
        region: Optional[str] = None,
        contact_person: Optional[str] = None,
        designation: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> int:
        """Insert a newly accepted lead into public."TLead". Returns lead_id.

        ``linkedin_id`` is intentionally left untouched on insert
        (extraction has no source field for it) - never populated with
        fabricated data.

        Whether a lead is "new" is decided by the caller (by matching
        against ``fetch_all()`` output via the existing dedup keys) -
        this method performs the insert unconditionally, and only ever
        adds a row; it never updates or removes the 136 pre-existing
        historical rows. See backfill_blank_fields() for the update path
        used instead when the lead already exists.
        """
        with closing(self._connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public."TLead"
                        (company_name, gst, turn_over, website_url, mobile_number,
                         alternate_mobile_number, email_id, city, address,
                         industry_type, region, contact_person, designation,
                         remarks)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING lead_id;
                    """,
                    (
                        company_name,
                        gst,
                        turnover,
                        website_url,
                        mobile_number,
                        alternate_mobile_number,
                        email_id,
                        city,
                        address,
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

    def backfill_blank_fields(
        self,
        lead_id: int,
        *,
        gst: Optional[str] = None,
        turnover: Optional[float] = None,  # NUMERIC in the database - see save()'s turnover param.
        website_url: Optional[str] = None,
        mobile_number: Optional[str] = None,
        alternate_mobile_number: Optional[str] = None,
        email_id: Optional[str] = None,
        city: Optional[str] = None,
        address: Optional[str] = None,
        industry_type: Optional[str] = None,
        region: Optional[str] = None,
        contact_person: Optional[str] = None,
        designation: Optional[str] = None,
        remarks: Optional[str] = None,
    ) -> None:
        """Fills in blank/NULL columns on an EXISTING lead row with newly
        found values, without ever overwriting a column that already has
        data - a later run finding a company's GST for the first time
        should not lose that just because the company was already in
        public."TLead" (e.g. one of the 136 historical baseline rows) with
        that field empty. Only fields passed here with a truthy value are
        even considered; anything not given, or empty, leaves the
        existing column exactly as it was either way.
        """
        candidates = {
            "gst": gst, "turn_over": turnover, "website_url": website_url,
            "mobile_number": mobile_number, "alternate_mobile_number": alternate_mobile_number,
            "email_id": email_id, "city": city, "address": address,
            "industry_type": industry_type, "region": region,
            "contact_person": contact_person, "designation": designation,
            "remarks": remarks,
        }
        updates = {column: value for column, value in candidates.items() if value}
        if not updates:
            return

        # turn_over is NUMERIC, not text - NULLIF(turn_over, '') itself
        # raises InvalidTextRepresentation (Postgres tries to parse '' as
        # a number to compare it), so it needs a plain NULL-only check;
        # every other column here is text-typed, where an empty string is
        # exactly as "blank" as NULL and both must be treated as fillable.
        set_clause = ", ".join(
            f"{column} = COALESCE({column}, %({column})s)" if column == "turn_over"
            else f"{column} = COALESCE(NULLIF({column}, ''), %({column})s)"
            for column in updates
        )
        with closing(self._connect()) as conn, conn:
            with conn.cursor() as cur:
                cur.execute(
                    f'UPDATE public."TLead" SET {set_clause} WHERE lead_id = %(lead_id)s;',
                    {**updates, "lead_id": lead_id},
                )
        logger.info("mlead: backfilled blank field(s) %s for lead_id=%s", sorted(updates), lead_id)
