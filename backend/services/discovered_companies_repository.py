"""Read-only company discovery from the existing discovered_companies table."""

from __future__ import annotations

from contextlib import closing
import json
import re
import unicodedata
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from config.geography import (
    CITY_LOCALITIES,
    CITY_STATE,
    canonical_city,
    canonical_locality,
    classify_location,
    city_for_locality,
    location_query_variants,
    parse_address_components,
)
from config.targeting import match_target_industry
from core.config import settings
from schemas.company import Company
from schemas.search_request import SearchRequest
from services.company_identity import company_composite_key, is_generic_maps_label
from services.contact_extraction.name_rules import is_person_name


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
        "locations",
    )

    @staticmethod
    def _is_broad_manufacturing_request(industry: str) -> bool:
        """Return whether the request names manufacturing without a subtype.

        Specific phrases such as "textile manufacturing" remain industry
        filters even if the taxonomy only recognizes the generic word.
        """
        if match_target_industry(industry) != "Manufacturing":
            return False
        words = set(industry.casefold().replace("&", " ").split())
        generic_words = {
            "manufacturing", "manufacturer", "manufacturers", "industry",
            "industries", "company", "companies", "business", "businesses",
            "in", "the", "all", "of",
        }
        return not (words - generic_words)

    @classmethod
    def _industry_filter_terms(cls, industry: str) -> list[str]:
        """Return DB industry terms, or none for a location-wide broad search."""
        industry = (industry or "").strip()
        if not industry or cls._is_broad_manufacturing_request(industry):
            return []

        industry_match = match_target_industry(industry)
        return [
            industry_match
            if industry_match and industry_match != "Manufacturing"
            else industry
        ]

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

    def search(self, request: SearchRequest, *, offset: int = 0, limit: Optional[int] = None) -> list[Company]:
        with closing(self._connect()) as conn:
            conn.set_session(readonly=True)
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                table = sql.SQL("{}.{}").format(
                    sql.Identifier("public"), sql.Identifier("discovered_companies")
                )
                cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(table))
                columns = [description.name for description in cur.description]
                column_lookup = {column.lower(): column for column in columns}
                cur.execute(
                    """SELECT kcu.column_name
                       FROM information_schema.table_constraints tc
                       JOIN information_schema.key_column_usage kcu
                         ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                        AND tc.table_name = kcu.table_name
                       WHERE tc.table_schema = 'public'
                         AND tc.table_name = 'discovered_companies'
                         AND tc.constraint_type = 'PRIMARY KEY'
                       ORDER BY kcu.ordinal_position"""
                )
                # RealDictCursor returns mappings, so access the selected
                # information_schema column by name rather than position.
                primary_key = [row["column_name"] for row in cur.fetchall()]
                if not primary_key:
                    primary_key = [
                        column_lookup[name]
                        for name in ("id", "row_id", "discovered_company_id", "company_id")
                        if name in column_lookup
                    ][:1]
                field_columns = self._field_columns(column_lookup)

                name_column = field_columns.get("company_name")
                if not name_column:
                    raise RuntimeError(
                        "public.discovered_companies has no recognized company-name column."
                    )

                conditions = []
                parameters: list[Any] = []
                location = (request.location or "").strip()
                if location:
                    # The discovered_companies.locations JSONB field is the
                    # authoritative geography for this source. Do not let a
                    # conflicting city/state/region field pull an unrelated
                    # row into a location-scoped search.
                    location_columns = (
                        [column_lookup["locations"]]
                        if "locations" in column_lookup
                        else self._matching_columns(
                            column_lookup, self._LOCATION_FILTER_COLUMNS
                        )
                    )
                    if not location_columns:
                        raise RuntimeError(
                            "public.discovered_companies has no recognized location column."
                        )
                    components = [part.strip() for part in location.split(",") if part.strip()]
                    if len(components) > 1:
                        # A combined request (for example, "Valatti,
                        # Villupuram") must match every component in the same
                        # row, while each component may occur in its own JSON
                        # location-array element.
                        term_groups = [[component] for component in components]
                    else:
                        # A city-only request remains broad and retains the
                        # existing locality variants for that city.
                        terms = [term for term in location_query_variants(location) if term]
                        city = canonical_city(location)
                        if city and self._normalize_location_key(location) == self._normalize_location_key(city):
                            terms.extend(CITY_LOCALITIES.get(city, []))
                        term_groups = [list(dict.fromkeys(terms))]

                    # Resolve plain-text spellings to canonical spellings in
                    # the authoritative JSONB locations array. This is
                    # read-only and only adds equivalent stored spellings to
                    # the existing location filter.
                    locations_column = column_lookup.get("locations")
                    if locations_column:
                        location_identifier = sql.Identifier(
                            "source_row", locations_column
                        )
                        cur.execute(
                            sql.SQL(
                                """SELECT DISTINCT location_item.value
                                   FROM {} AS source_row
                                   CROSS JOIN LATERAL jsonb_array_elements_text(
                                       CASE
                                           WHEN jsonb_typeof({location}::jsonb) = 'array'
                                               THEN {location}::jsonb
                                           ELSE jsonb_build_array({location}::jsonb)
                                       END
                                   ) AS location_item(value)
                                   WHERE location_item.value IS NOT NULL"""
                            ).format(
                                table,
                                location=location_identifier,
                            )
                        )
                        stored_locations = [
                            row["value"]
                            for row in cur.fetchall()
                            if row.get("value") is not None
                        ]
                        term_groups = self._resolve_location_term_groups(
                            term_groups,
                            stored_locations,
                        )

                    group_conditions = []
                    values: list[Any] = []
                    for terms in term_groups:
                        group_condition, group_values = self._location_text_match(
                            location_columns, terms
                        )
                        group_conditions.append(group_condition)
                        values.extend(group_values)
                    condition = sql.SQL("(") + sql.SQL(" AND ").join(group_conditions) + sql.SQL(")")
                    conditions.append(condition)
                    parameters.extend(values)

                industry = (request.industry or "").strip()
                industry_terms = self._industry_filter_terms(industry)
                if industry_terms:
                    industry_columns = self._matching_columns(
                        column_lookup, self._INDUSTRY_FILTER_COLUMNS
                    )
                    if not industry_columns:
                        raise RuntimeError(
                            "public.discovered_companies has no recognized industry column."
                        )
                    # Specific industry requests constrain the DB query.
                    # A generic manufacturing request is location-wide so it
                    # can include every stored industry label (including
                    # categories outside the configured taxonomy).
                    condition, values = self._text_match(industry_columns, industry_terms)
                    conditions.append(condition)
                    parameters.extend(values)

                where_clause = (
                    sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
                    if conditions else sql.SQL("")
                )
                query = sql.SQL("SELECT * FROM {}{}").format(table, where_clause)
                order_columns = [name_column] + [column for column in primary_key if column != name_column]
                query += sql.SQL(" ORDER BY {} LIMIT %s OFFSET %s").format(
                    sql.SQL(", ").join(sql.Identifier(column) for column in order_columns)
                )
                page_limit = max(1, min(limit or max(1, request.max_results) * 10, 10000))
                parameters.extend([page_limit, max(0, offset)])
                cur.execute(query, parameters)
                rows = [dict(row) for row in cur.fetchall()]

        metadata_columns = [
            column for column in columns
            if any(token in column.lower() for token in (
                "source", "provider", "crawler", "provenance", "discovered_url",
            ))
        ]
        companies = []
        for row in rows:
            company = self._row_to_company(row, field_columns, requested_location=request.location)
            companies.append(self._attach_provenance(company, row, primary_key, metadata_columns))
        return companies

    @staticmethod
    def _source_data(company: Company) -> dict[str, Any]:
        value = company.source_metadata.get("source_data") if company.source_metadata else None
        return value if isinstance(value, dict) else {}

    @classmethod
    def _eligibility_reason(cls, company: Company, request: SearchRequest) -> Optional[str]:
        """Do not apply Marketing validation rules during source retrieval.

        Discovery rows are selected using their stored industry/location
        columns. Verification, geographic eligibility, and existing-record
        checks belong to the unchanged downstream Marketing validation stage.
        """
        return None

    @staticmethod
    def _attach_provenance(
        company: Company,
        row: dict[str, Any],
        primary_key: list[str],
        metadata_columns: list[str],
    ) -> Company:
        row_id = "|".join(
            f"{column}={row.get(column)}"
            for column in primary_key if row.get(column) is not None
        )
        metadata = {
            column: row.get(column)
            for column in metadata_columns if row.get(column) is not None
        }
        metadata.update(company.source_metadata or {})
        metadata["_source_company_name"] = company.company_name
        metadata["_source_locations"] = next(
            (value for column, value in row.items() if column.lower() == "locations"),
            None,
        )
        return company.model_copy(update={
            "source_table": "public.discovered_companies",
            "source_row_id": row_id or None,
            "source_metadata": metadata,
        })

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

    @staticmethod
    def _location_text_match(columns: list[str], terms: list[str]):
        """Match terms against text location columns or JSON array elements."""
        comparisons = []
        parameters: list[str] = []
        for column in columns:
            for term in terms:
                escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                if column.lower() == "locations":
                    comparison = sql.SQL(
                        """EXISTS (
                               SELECT 1
                               FROM jsonb_array_elements_text(
                                   CASE
                                       WHEN jsonb_typeof({column}::jsonb) = 'array'
                                           THEN {column}::jsonb
                                       ELSE jsonb_build_array({column}::jsonb)
                                   END
                               ) AS location_item(value)
                               CROSS JOIN LATERAL regexp_split_to_table(location_item.value, ',') AS location_part(value)
                               WHERE LOWER(BTRIM(location_part.value)) = LOWER(%s)
                           )"""
                    ).format(column=sql.Identifier(column))
                else:
                    comparison = sql.SQL("CAST({} AS TEXT) ILIKE %s").format(
                        sql.Identifier(column)
                    )
                comparisons.append(comparison)
                # Match exact hierarchy components whether stored as separate
                # array items or in one comma-separated location item.
                parameters.append(term if column.lower() == "locations" else f"%{escaped}%")
        return sql.SQL("(") + sql.SQL(" OR ").join(comparisons) + sql.SQL(")"), parameters

    @staticmethod
    def _normalize_location_key(value: str) -> str:
        """Normalize accents and punctuation for matching user and DB labels."""
        decomposed = unicodedata.normalize("NFKD", value).casefold()
        without_marks = "".join(
            character
            for character in decomposed
            if not unicodedata.category(character).startswith("M")
        )
        return " ".join(re.sub(r"[^\w]+", " ", without_marks).split())

    @classmethod
    def _resolve_location_term_groups(
        cls,
        term_groups: list[list[str]],
        stored_locations: list[str],
    ) -> list[list[str]]:
        """Add canonical DB spellings for exact accent-insensitive matches.

        Keep the original terms so existing substring matching and location
        behavior continue to work. A normalized equality check avoids
        resolving a short location name to an unrelated longer place name.
        """
        normalized_locations: dict[str, list[str]] = {}
        for stored_location in stored_locations:
            key = cls._normalize_location_key(stored_location)
            if key:
                normalized_locations.setdefault(key, []).append(stored_location)

        resolved_groups = []
        for terms in term_groups:
            resolved_terms = list(terms)
            for term in terms:
                key = cls._normalize_location_key(term)
                if key:
                    resolved_terms.extend(normalized_locations.get(key, []))
            resolved_groups.append(list(dict.fromkeys(resolved_terms)))
        return resolved_groups

    @classmethod
    def _row_to_company(
        cls,
        row: dict[str, Any],
        field_columns: dict[str, str],
        *,
        requested_location: Optional[str] = None,
    ) -> Company:
        values = {}
        address_column = field_columns.get("address")
        raw_address_values = row.get(address_column) if address_column else None
        if isinstance(raw_address_values, list):
            address_values = [
                str(item).strip()
                for item in raw_address_values
                if item is not None and str(item).strip()
            ]
        elif isinstance(raw_address_values, str):
            try:
                decoded_addresses = json.loads(raw_address_values)
            except (TypeError, json.JSONDecodeError):
                decoded_addresses = raw_address_values
            address_values = (
                [str(item).strip() for item in decoded_addresses if item is not None and str(item).strip()]
                if isinstance(decoded_addresses, list)
                else [str(decoded_addresses).strip()]
            )
        else:
            address_values = []

        for field, column in field_columns.items():
            raw_value = row.get(column)
            if isinstance(raw_value, list):
                usable = [str(item).strip() for item in raw_value if item is not None and str(item).strip()]
                if field == "address":
                    raw_value = "; ".join(usable)
                elif field == "contact_person":
                    raw_value = next((item for item in usable if is_person_name(item)), None)
                else:
                    raw_value = usable[0] if usable else None
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
        raw_locations = next(
            (value for column, value in row.items() if column.lower() == "locations"),
            None,
        )
        if isinstance(raw_locations, list):
            location_values = [str(item).strip() for item in raw_locations if item is not None and str(item).strip()]
        elif isinstance(raw_locations, str):
            try:
                decoded_locations = json.loads(raw_locations)
            except (TypeError, json.JSONDecodeError):
                decoded_locations = raw_locations
            location_values = (
                [str(item).strip() for item in decoded_locations if item is not None and str(item).strip()]
                if isinstance(decoded_locations, list)
                else [str(decoded_locations).strip()]
            )
        else:
            location_values = []

        location_resolution_issue = None
        if requested_location:
            address_matches = []
            address_decisions = []
            for address_value in address_values:
                decision, _ = classify_location(
                    None, None, address_value, requested_location
                )
                address_decisions.append(decision)
                if decision == "match":
                    address_matches.append(address_value)

            if address_matches:
                # Legacy rows can aggregate several branches or website
                # addresses. Keep the address that confirms this search
                # instead of deriving geography from the combined string.
                values["address"] = address_matches[0]
                address_city, address_state = parse_address_components(address_matches[0])
                requested_city = canonical_city(requested_location)
                values["city"] = address_city or requested_city or values.get("city")
                values["state"] = (
                    address_state
                    or CITY_STATE.get(canonical_city(values.get("city")) or values.get("city"))
                    or values.get("state")
                )
                values["region"] = values["state"] or values.get("region")
            elif len(location_values) > 2 or (
                address_decisions and all(item == "outside" for item in address_decisions)
            ):
                # The old multi-location shape is ambiguous unless an
                # individual address confirms the requested place. Keep the
                # row for diagnostics, but do not treat it as a target-city
                # lead downstream.
                location_resolution_issue = (
                    f"legacy multi-location record has no address confirming '{requested_location}'"
                )

        # Preserve the specifically searched locality only when that locality
        # is present in this Discovery DB row's stored location hierarchy.
        # This lets Marketing store/display "Ambattur, Chennai" without
        # mistaking every locality in a city for the requested one.
        requested_locality = canonical_locality(requested_location)
        requested_location_part = (requested_location or "").split(",", 1)[0].strip()
        requested_locality_key = cls._normalize_location_key(
            requested_locality or requested_location_part
        )
        matched_locality = next(
            (
                canonical_locality(item) or item
                for item in location_values
                if item
                and not canonical_city(item)
                and requested_locality_key
                and cls._normalize_location_key(item) == requested_locality_key
            ),
            None,
        )
        if matched_locality:
            values["locality"] = matched_locality
        if not values.get("city"):
            # The discovery `locations` array is also used as the authoritative
            # search geography. When the separate city field and address do
            # not identify a city, recover the recognized parent city from
            # that same stored hierarchy (e.g. ["Ambattur", "Chennai"] ->
            # city="Chennai"). This populates the lead/export model without
            # writing back to the discovery database.
            values["city"] = next(
                (city for item in location_values if (city := canonical_city(item))),
                None,
            )
            if not values.get("city"):
                values["city"] = next(
                    (
                        parent_city
                        for item in location_values
                        if (parent_city := city_for_locality(canonical_locality(item)))
                    ),
                    None,
                )
            if not values.get("city"):
                requested_locality = canonical_locality(requested_location)
                values["city"] = city_for_locality(requested_locality)
            address_text = (values.get("address") or "").casefold()
            requested = (requested_location or "").strip()
            if not values["city"] and requested and requested.casefold() in address_text:
                values["city"] = requested
        if not values.get("state"):
            values["state"] = parsed_state
        if not values.get("region"):
            values["region"] = parsed_state
        metadata = (
            {"_location_resolution_issue": location_resolution_issue}
            if location_resolution_issue else {}
        )
        return Company(**values, source_metadata=metadata)


class DiscoveredCompaniesSearchService:
    """Search adapter matching the existing SearchAgent service contract."""

    def __init__(self, repository: Optional[DiscoveredCompaniesRepository] = None):
        self.repository = repository or DiscoveredCompaniesRepository()
        self.last_run_stats = {"raw_fetched": 0, "after_dedup": 0, "removed": []}

    def search(self, request: SearchRequest) -> list[Company]:
        if isinstance(self.repository, DiscoveredCompaniesRepository):
            # Read every matching source page. The workflow later stops
            # enrichment/validation as soon as it has enough new leads, but
            # must be able to continue beyond the first candidate page when
            # earlier companies are duplicates or fail validation.
            raw_companies = []
            offset = 0
            page_size = 10000
            while True:
                page = self.repository.search(request, limit=page_size, offset=offset)
                raw_companies.extend(page)
                if len(page) < page_size:
                    break
                offset += len(page)
        else:
            # Existing injected repository adapters keep their one-argument contract.
            raw_companies = self.repository.search(request)
        companies: list[Company] = []
        seen: dict[str, Company] = {}
        seen_indexes: dict[str, int] = {}
        removed = []
        filtered_labels: dict[str, list[Company]] = {}
        for company in raw_companies:
            eligibility_reason = DiscoveredCompaniesRepository._eligibility_reason(company, request)
            if eligibility_reason:
                removed.append({
                    "company_name": company.company_name or "(unnamed)",
                    "stage": "search_filter", "reason": f"rejected: {eligibility_reason}",
                    "source_table": company.source_table, "source_row_id": company.source_row_id,
                })
                continue
            if is_generic_maps_label(company.company_name):
                label = " ".join((company.company_name or "(unnamed)").casefold().split())
                filtered_labels.setdefault(label, []).append(company)
                continue
            # For source-stage duplicate grouping, use the same locations
            # field that constrained the database query. A conflicting city
            # field (for example Hyderabad) must not mislabel a Villupuram
            # source row in the dedup reason. Downstream validation keeps its
            # existing company city based comparison.
            key = company_composite_key(
                company,
                prefer_source_locations=True,
                normalize_legal_suffixes=True,
            )
            prior = seen.get(key) if key else None
            if prior is None:
                companies.append(company)
                if key:
                    seen[key] = company
                    seen_indexes[key] = len(companies) - 1
                continue
            # Keep one candidate per real company, but merge fields that are
            # blank on the retained row from its duplicate source rows.
            merge_fields = (
                "website", "phone", "phone_alt", "email", "address", "city",
                "state", "district", "locality", "turnover", "employee_count",
                "gst", "cin", "region", "remarks", "contact_person",
                "designation", "linkedin_url",
            )
            updates = {
                field: getattr(company, field)
                for field in merge_fields
                if not getattr(prior, field) and getattr(company, field)
            }
            source_metadata = dict(prior.source_metadata or {})
            duplicate_ids = list(source_metadata.get("_duplicate_source_row_ids", []))
            if company.source_row_id:
                duplicate_ids.append(str(company.source_row_id))
            source_metadata["_duplicate_source_row_ids"] = list(dict.fromkeys(duplicate_ids))
            updates["source_metadata"] = source_metadata
            merged = prior.model_copy(update=updates)
            companies[seen_indexes[key]] = merged
            seen[key] = merged
            removed.append({
                "company_name": company.company_name or "(unnamed)",
                "stage": "search_dedup",
                "reason": (
                    "duplicate discovered-company source row; its missing details were merged "
                    "into the retained company candidate, which continues through enrichment "
                    "and validation"
                ),
                "source_table": company.source_table,
                "source_row_id": company.source_row_id,
                "matched_identity_key": key,
                "matched_source": prior.source_table,
                "matched_record_id": prior.source_row_id,
            })
        for matches in filtered_labels.values():
            first = matches[0]
            source_row_ids = [item.source_row_id for item in matches if item.source_row_id]
            removed.append({
                "company_name": first.company_name or "(unnamed)",
                "stage": "search_filter",
                "reason": (
                    "rejected: search result contains a generic Google Maps interface label, "
                    "not a business name"
                ),
                "source_table": first.source_table,
                "source_row_id": first.source_row_id,
                "source_row_ids": source_row_ids,
                "occurrence_count": len(matches),
            })
        self.last_run_stats = {
            "raw_fetched": len(raw_companies),
            "after_dedup": len(companies),
            "removed": removed,
        }
        return companies
