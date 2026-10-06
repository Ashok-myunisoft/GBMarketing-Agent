"""Read-only company discovery from the existing discovered_companies table."""

from __future__ import annotations

from contextlib import closing
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from config.geography import location_query_variants, parse_address_components
from config.targeting import search_industry_queries
from core.config import settings
from schemas.company import Company
from schemas.search_request import SearchRequest


class DiscoveredCompaniesRepository:
    """Queries the discovery database without modifying its rows."""

    _COLUMN_ALIASES = {
        "company_name": ("company_name", "companyname", "name", "business_name", "legal_name", "company"),
        "website": ("website", "website_url", "website_link", "url", "domain"),
        "phone": ("phone", "phone_number", "primary_phone", "mobile", "mobile_number", "telephone", "phones"),
        "phone_alt": ("phone_alt", "alternate_phone", "alternate_mobile_number", "secondary_phone"),
        "email": ("email", "email_id", "email_address", "emails"),
        "address": ("address", "full_address", "registered_address", "company_address", "addresses", "locations"),
        "city": ("city", "city_name", "company_city", "town"),
        "state": ("state", "state_name", "province"),
        "industry": ("industry", "industry_type", "industry_name", "industry_segment", "sector", "sector_name", "business_type", "business_category", "category"),
        "turnover": ("turnover", "turn_over", "turnover_amount", "annual_turnover", "revenue"),
        "gst": ("gst", "gstin", "gst_number", "gst_no", "gstin_number", "gst_data"),
        "cin": ("cin", "cin_number", "cin_data"),
        "region": ("region", "state", "state_name"),
        "contact_person": ("contact_person", "contact_name", "person_name", "contact_person_name"),
        "designation": ("designation", "job_title", "position"),
        "linkedin_url": ("linkedin_url", "linkedin", "linkedin_id"),
        "remarks": ("remarks", "notes"),
    }
    _INDUSTRY_FILTER_COLUMNS = _COLUMN_ALIASES["industry"]
    _LOCATION_FILTER_COLUMNS = (
        "city", "city_name", "company_city", "town", "locality", "district", "district_name",
        "state", "state_name", "region", "location", "location_name", "company_location",
        "locations", "address", "full_address", "registered_address", "company_address", "addresses",
    )

    def __init__(self, dsn: Optional[str] = None):
        self._dsn = dsn if dsn is not None else settings.DISCOVERED_COMPANIES_DATABASE_URL

    def _connect(self):
        if not self._dsn:
            raise RuntimeError(
                "DISCOVERED_COMPANIES_DATABASE_URL is not configured."
            )
        # The provided URL uses SQLAlchemy's driver-qualified spelling;
        # psycopg2/libpq expects the plain PostgreSQL URI scheme.
        dsn = self._dsn.replace(
            "postgresql+psycopg2://", "postgresql://", 1
        ).replace("postgres+psycopg2://", "postgresql://", 1)
        return psycopg2.connect(dsn)

    def search(self, request: SearchRequest) -> list[Company]:
        with closing(self._connect()) as conn:
            conn.set_session(readonly=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                table = sql.SQL("{}.{}").format(
                    sql.Identifier("public"), sql.Identifier("discovered_companies")
                )
                cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table))
                columns = [description.name for description in cur.description]
                column_lookup = {column.lower(): column for column in columns}
                field_columns = self._field_columns(column_lookup)

                name_column = field_columns.get("company_name")
                if not name_column:
                    raise RuntimeError(
                        "public.discovered_companies has no recognized company-name column."
                    )

                conditions = []
                parameters: list[Any] = []
                industry = (request.industry or "").strip()
                if industry:
                    industry_columns = self._matching_columns(
                        column_lookup, self._INDUSTRY_FILTER_COLUMNS
                    )
                    if not industry_columns:
                        raise RuntimeError(
                            "public.discovered_companies has no recognized industry column."
                        )
                    terms = list(dict.fromkeys([industry, *search_industry_queries(industry)]))
                    condition, values = self._text_match(industry_columns, terms)
                    conditions.append(condition)
                    parameters.extend(values)

                location = (request.location or "").strip()
                if location:
                    location_columns = self._matching_columns(
                        column_lookup, self._LOCATION_FILTER_COLUMNS
                    )
                    if not location_columns:
                        raise RuntimeError(
                            "public.discovered_companies has no recognized location/address column."
                        )
                    terms = [term for term in location_query_variants(location) if term]
                    condition, values = self._text_match(location_columns, terms)
                    conditions.append(condition)
                    parameters.extend(values)

                where_clause = (
                    sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
                    if conditions else sql.SQL("")
                )
                query = sql.SQL("SELECT * FROM {}{}").format(table, where_clause)
                query += sql.SQL(" ORDER BY {} LIMIT %s").format(
                    sql.Identifier(name_column)
                )
                parameters.append(max(1, request.max_results))
                cur.execute(query, parameters)
                rows = [dict(row) for row in cur.fetchall()]

        return [
            self._row_to_company(row, field_columns, requested_location=request.location)
            for row in rows
        ]

    @classmethod
    def _field_columns(cls, column_lookup: dict[str, str]) -> dict[str, str]:
        fields: dict[str, str] = {}
        for field, aliases in cls._COLUMN_ALIASES.items():
            for alias in aliases:
                actual = column_lookup.get(alias)
                if actual:
                    fields[field] = actual
                    break
        return fields

    @staticmethod
    def _matching_columns(
        column_lookup: dict[str, str], aliases: tuple[str, ...]
    ) -> list[str]:
        return list(dict.fromkeys(
            column_lookup[alias]
            for alias in aliases
            if alias in column_lookup
        ))

    @staticmethod
    def _text_match(columns: list[str], terms: list[str]):
        comparisons = []
        parameters: list[str] = []
        for column in columns:
            for term in terms:
                comparisons.append(
                    sql.SQL("CAST({} AS TEXT) ILIKE %s").format(sql.Identifier(column))
                )
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                parameters.append(f"%{escaped}%")
        return sql.SQL("(") + sql.SQL(" OR ").join(comparisons) + sql.SQL(")"), parameters

    @classmethod
    def _row_to_company(
        cls,
        row: dict[str, Any],
        field_columns: dict[str, str],
        *,
        requested_location: Optional[str] = None,
    ) -> Company:
        values = {}
        for field, column in field_columns.items():
            raw_value = row.get(column)
            if isinstance(raw_value, list):
                usable = [str(item).strip() for item in raw_value if item is not None and str(item).strip()]
                raw_value = "; ".join(usable) if field == "address" else (usable[0] if usable else None)
            values[field] = raw_value
        values["company_name"] = str(values.get("company_name") or "").strip()
        values["turnover"] = (
            str(values["turnover"]) if values.get("turnover") is not None else None
        )
        for field in (
            "website", "phone", "phone_alt", "email", "address", "city", "state",
            "industry", "gst", "cin", "region", "contact_person", "designation",
            "linkedin_url", "remarks",
        ):
            if values.get(field) is not None:
                values[field] = str(values[field]).strip() or None
        parsed_city, parsed_state = parse_address_components(values.get("address"))
        if not values.get("city"):
            values["city"] = parsed_city
            address_text = (values.get("address") or "").casefold()
            requested = (requested_location or "").strip()
            if not values["city"] and requested and requested.casefold() in address_text:
                values["city"] = requested
        if not values.get("state"):
            values["state"] = parsed_state
        if not values.get("region"):
            values["region"] = parsed_state
        return Company(**values)


class DiscoveredCompaniesSearchService:
    """Search adapter matching the existing SearchAgent service contract."""

    def __init__(self, repository: Optional[DiscoveredCompaniesRepository] = None):
        self.repository = repository or DiscoveredCompaniesRepository()
        self.last_run_stats = {"raw_fetched": 0, "after_dedup": 0, "removed": []}

    def search(self, request: SearchRequest) -> list[Company]:
        companies = self.repository.search(request)
        self.last_run_stats = {
            "raw_fetched": len(companies),
            "after_dedup": len(companies),
            "removed": [],
        }
        return companies
