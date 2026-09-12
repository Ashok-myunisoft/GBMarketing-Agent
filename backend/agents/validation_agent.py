"""Validation and duplicate removal for discovered companies."""

import csv
import re
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

import logging

from agents.base_agent import BaseClass
from config.geography import classify_location
from config.targeting import match_target_industry, parse_turnover_range, turnover_to_crore
from schemas.company import Company
from services.geocoding_service import GeoapifyGeocodingService
from services.mlead.repository import MleadRepository


logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_EXISTING_DATA_DIR = BACKEND_DIR / "Existing-data"
MASTER_EXPORT_PATH = BACKEND_DIR / "exports" / "All_Extracted_Leads.xlsx"

# Single shared repository instance - a thin, stateless-per-call wrapper
# around a PostgreSQL connection to the EXISTING production table
# public."TLead" (see services/mlead/repository.py). Not a new table.
_mlead_repository = MleadRepository()


class ValidationAgent(BaseClass):
    """Rejects only records that conflict with an established lead baseline.

    Public directories rarely expose financials or headcount, so missing data is
    marked as unverified instead of silently excluding an otherwise useful lead.
    """

    def __init__(self, geocoder: Optional[GeoapifyGeocodingService] = None):
        self._geocoder = geocoder or GeoapifyGeocodingService()

    def execute(
        self,
        companies: list[Company],
        *,
        existing_excel_path: Optional[str] = None,
        requested_location: Optional[str] = None,
        requested_industry: Optional[str] = None,
        requested_turnover_floor_cr: Optional[float] = None,
        requested_employee_floor: Optional[float] = None,
        requested_gst_required: bool = False,
    ) -> list[Company]:
        source = Path(existing_excel_path) if existing_excel_path else DEFAULT_EXISTING_DATA_DIR
        existing_keys = self._load_existing_keys(source)
        if MASTER_EXPORT_PATH.exists():
            existing_keys.update(self._load_existing_keys(MASTER_EXPORT_PATH))

        # Persistent baseline: existing production leads in PostgreSQL
        # public."TLead" (~136 historical rows plus anything since accepted),
        # so history survives container stop/restart/recreate (the
        # file-based baseline above lives inside the container). Reuses
        # the exact same _company_keys matching used everywhere else in
        # this method - it does not introduce a second matching rule.
        postgres_key_to_id: dict[str, int] = {}
        for row in self._load_mlead_rows():
            history_company = Company(
                company_name=row.get("company_name") or "",
                gst=row.get("gst"),
                website=row.get("website_url"),
                email=row.get("email_id"),
                phone=row.get("mobile_number"),
                phone_alt=row.get("alternate_mobile_number"),
                city=row.get("city"),
                industry=row.get("industry_type"),
                region=row.get("region"),
                contact_person=row.get("contact_person"),
                designation=row.get("designation"),
            )
            for key in self._company_keys(history_company):
                existing_keys.add(key)
                postgres_key_to_id[key] = row["lead_id"]

        kept: list[Company] = []
        duplicate_count = 0
        rejected_count = 0
        out_of_area_count = 0
        location_unknown_count = 0
        no_identifier_count = 0
        removed: list[dict] = []

        # Geocoded once here, for the whole run - never per company - since
        # `requested_location` is the same string for every company in this
        # batch. Lets classify_location() work at full district/city
        # precision for a requested city outside the small hand-picked
        # CITY_ALIASES/CITY_DISTRICTS seed lists (e.g. "Nagpur") instead of
        # only the narrower state-level fallback those lists leave it to.
        # A no-op (None) whenever GEOAPIFY_API_KEY isn't configured or the
        # lookup fails - every classify_location call below already
        # degrades to its pre-existing seed-list-only behavior in that case.
        requested_geocoded = self._geocoder.geocode(requested_location) if requested_location else None

        for company in companies:
            name = company.company_name or "(unnamed)"
            keys = self._company_keys(company)
            if not keys:
                no_identifier_count += 1
                removed.append({
                    "company_name": name,
                    "reason": "no identifying details (name, GST, website, phone, or email) to validate against",
                })
                continue
            # Do not remove one newly extracted result merely because another
            # provider/locality returned it in this same run.  Validation's
            # deduplication authority is the established export/database
            # baseline below; search-stage deduplication remains responsible
            # for collapsing obvious name+website repeats.
            if keys & existing_keys:
                duplicate_count += 1
                removed.append({
                    "company_name": name,
                    "reason": "already present in the existing export / dedup baseline",
                })
                # This run's enrichment may have found a field (GST,
                # turnover, contact info, ...) the existing mlead row never
                # had - backfill it even though this company isn't a new
                # lead, rather than silently discarding freshly-found data
                # just because it isn't new. Only applies when the match is
                # against the mlead baseline specifically (postgres_key_to_id) -
                # a match against only the file-based baseline has no live
                # database row to update.
                existing_lead_id = next(
                    (postgres_key_to_id[key] for key in keys if key in postgres_key_to_id), None
                )
                if existing_lead_id is not None:
                    self._backfill_mlead(existing_lead_id, company)
                continue

            location_note = None
            if requested_location:
                decision, reason = classify_location(
                    company.city,
                    company.state,
                    company.address,
                    requested_location,
                    company_district=company.district,
                    company_locality=company.locality,
                    requested_geocoded_state=requested_geocoded.state if requested_geocoded else None,
                    requested_geocoded_district=requested_geocoded.district if requested_geocoded else None,
                    requested_geocoded_city=requested_geocoded.city if requested_geocoded else None,
                )
                logger.info(
                    "[LOCATION] requested=%s company=%s resolved_city=%s resolved_state=%s decision=%s reason=%s",
                    requested_location, name, company.city, company.state, decision, reason,
                )
                if decision == "outside":
                    out_of_area_count += 1
                    removed.append({
                        "company_name": name,
                        "reason": (
                            f"located in '{company.city or company.address or 'an unknown location'}', "
                            f"outside the requested area '{requested_location}' ({reason})"
                        ),
                    })
                    continue
                if decision == "unknown":
                    location_unknown_count += 1
                    location_note = "unverified: location could not be confirmed against the requested area"

            notes = self._validation_notes(
                company,
                requested_industry,
                requested_turnover_floor_cr=requested_turnover_floor_cr,
                requested_employee_floor=requested_employee_floor,
                requested_gst_required=requested_gst_required,
            )
            if location_note:
                notes.append(location_note)
            rejected_notes = [note for note in notes if note.startswith("rejected:")]
            rejected = bool(rejected_notes)
            status = "rejected" if rejected else ("validated" if not notes else "unverified")
            if not rejected:
                # A note enrichment already attached (e.g. a blocked GST
                # lookup) is appended to, never replaced by, validation's
                # own notes - both are independently useful to a reader.
                accepted = company.model_copy(update={
                    "validation_status": status,
                    "validation_notes": notes,
                    "remarks": "; ".join(note for note in (company.remarks, "; ".join(notes)) if note),
                })
                kept.append(accepted)
                existing_keys.update(keys)
                # This is the point the existing workflow treats a lead as
                # actually accepted (not merely discovered) - the correct
                # insertion point for the persistent PostgreSQL baseline.
                self._remember_in_mlead(accepted, keys, postgres_key_to_id)
            else:
                rejected_count += 1
                removed.append({
                    "company_name": name,
                    "reason": "; ".join(note.removeprefix("rejected: ") for note in rejected_notes),
                })

        # Stashed on the instance (not the return value) so callers that only
        # care about the kept list are unaffected; the orchestrator reads
        # these to report why a run added little/nothing new, and exactly
        # why each individual company was dropped.
        self.last_run_stats = {
            "scanned": len(companies),
            "new": len(kept),
            "duplicates": duplicate_count,
            "rejected": rejected_count,
            "out_of_area": out_of_area_count,
            "location_unknown": location_unknown_count,
            "no_identifiers": no_identifier_count,
        }
        self.removed_companies = removed

        return kept

    def _validation_notes(
        self,
        company: Company,
        requested_industry: Optional[str] = None,
        *,
        requested_turnover_floor_cr: Optional[float] = None,
        requested_employee_floor: Optional[float] = None,
        requested_gst_required: bool = False,
    ) -> list[str]:
        notes: list[str] = []
        requested_matched = match_target_industry(requested_industry) if requested_industry else None
        # Only enforced when the user asked for one specific segment (e.g.
        # "Pump"). A blank/generic ("Manufacturing") request is an
        # intentionally broad search across the whole taxonomy, so any
        # recognised target industry - or an unconfirmed one - is left as a
        # legitimate/unverified match rather than rejected.
        specific_request = bool(requested_matched and requested_matched != "Manufacturing")

        if company.industry:
            matched = match_target_industry(company.industry)
            if not matched:
                # An unmapped label (e.g. a generic Maps category like
                # "Manufacturer") isn't evidence the company is out of scope,
                # only that the label wasn't specific enough to check - so
                # this stays unverified, never rejected, either way.
                notes.append("unverified: declared industry could not be matched to a target segment")
            elif specific_request and matched != requested_matched:
                # A confirmed, different taxonomy entry is real disqualifying
                # evidence - the only industry case that stays a hard reject.
                notes.append(
                    f"rejected: declared industry '{matched}' does not match requested '{requested_matched}'"
                )
        else:
            # Missing data is not evidence of exclusion. Most discovery
            # providers never populate `industry` at all, so treating an
            # empty field as a rejection would drop legitimate leads purely
            # because enrichment didn't find a label - never do that.
            notes.append("unverified: industry not supplied")

        # Turnover/employee-count floors are opt-in: they only reject when the
        # user's own query explicitly asked for a size filter (see
        # PlannerAgent). Otherwise these fields are informational enrichment,
        # never a reason to drop an otherwise-valid lead.
        if company.turnover:
            min_cr, max_cr = parse_turnover_range(company.turnover)
            if min_cr is None:
                notes.append("unverified: turnover could not be parsed")
            elif requested_turnover_floor_cr is not None:
                floor = requested_turnover_floor_cr
                if max_cr is not None and max_cr < floor:
                    notes.append(f"rejected: turnover below {floor} Cr")
                elif min_cr < floor:
                    # Slab straddles the cutoff (e.g. "5 Cr to 25 Cr" vs a 10
                    # Cr floor) - GST lookups only ever return a range, never
                    # an exact figure, so this can't be resolved automatically.
                    notes.append(f"unverified: turnover slab '{company.turnover}' straddles {floor} Cr threshold - needs manual review")
        else:
            notes.append("unverified: turnover unavailable")

        employees = self._number(company.employee_count)
        if company.employee_count:
            if employees is None:
                notes.append("unverified: employee count could not be parsed")
            elif requested_employee_floor is not None and employees < requested_employee_floor:
                notes.append(f"rejected: employee count below {requested_employee_floor}")
        else:
            notes.append("unverified: employee count unavailable")

        if requested_gst_required and not company.gst:
            notes.append("rejected: GST number required but not found")

        return notes

    @staticmethod
    def _number(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        match = re.search(r"\d+(?:[,.]\d+)?", value.replace(",", ""))
        return float(match.group()) if match else None

    @staticmethod
    def _company_keys(company: Company) -> set[str]:
        keys: set[str] = set()
        if company.gst:
            normalized_gst = re.sub(r"\W+", "", company.gst).upper()
            keys.add(f"gst:{normalized_gst}")
        if company.website:
            parsed = urlparse(company.website if "://" in company.website else f"https://{company.website}")
            host = parsed.netloc.lower().removeprefix("www.")
            if host and "tradeindia.com" not in host:
                keys.add(f"website:{host}")
        if company.email:
            keys.add(f"email:{company.email.strip().lower()}")
        for number in (company.phone, company.phone_alt):
            if not number:
                continue
            digits = re.sub(r"\D+", "", number)
            if len(digits) >= 7:
                keys.add(f"phone:{digits[-10:]}")
        name = re.sub(r"\W+", "", company.company_name).lower()
        if name:
            keys.add(f"name:{name}")
        return keys

    def _load_mlead_rows(self) -> list[dict]:
        """Read the PostgreSQL public."TLead" baseline.

        Failures are logged and treated as "no persistent history for this
        run" rather than raised, so a temporarily unreachable database
        degrades gracefully to the existing file-based baseline instead of
        breaking the search/enrichment/validation/export pipeline. This is
        the documented fallback: if PostgreSQL is down, deduplication runs
        on the Existing-data/exports files only, exactly as it did before
        this change, until the database is reachable again.
        """
        try:
            return _mlead_repository.fetch_all()
        except Exception:
            logger.exception('mlead: failed to read PostgreSQL baseline (public."TLead"); continuing without it')
            return []

    def _remember_in_mlead(self, company: Company, keys: set[str], postgres_key_to_id: dict[str, int]) -> None:
        """Insert a newly accepted lead into public."TLead" so future runs
        (including after a container restart) recognise it via the
        existing dedup keys.

        Only ever called for a company that is NOT a match against the
        existing baseline - execute()'s dedup check handles the "already
        exists, but may still have new data worth saving" case itself via
        _backfill_mlead(), before a company could ever reach here as a
        duplicate. So this always performs a fresh insert, never a lookup.

        Never raises - a persistence failure must not fail an otherwise
        successful validation/export run, but is always logged clearly.
        """
        try:
            new_id = _mlead_repository.save(
                company_name=company.company_name or "",
                gst=company.gst,
                # public."TLead"'s turn_over column is NUMERIC - this
                # pipeline's turnover values are always a text label
                # ("128 Crore", "50 Million", a jamku slab like "5 Cr to
                # 25 Cr"), never a bare number, so the raw string can't be
                # written directly. turnover_to_crore() does real unit
                # conversion (128 Million is 12.8 Cr, not 128) and returns
                # the lower bound for a slab/range; None when nothing
                # convertible is found, which just leaves the column blank.
                turnover=turnover_to_crore(company.turnover),
                website_url=company.website,
                mobile_number=company.phone,
                alternate_mobile_number=company.phone_alt,
                email_id=company.email,
                city=company.city,
                address=company.address,
                industry_type=company.industry,
                region=company.region,
                contact_person=company.contact_person,
                designation=company.designation,
                remarks=company.remarks,
            )
            # Keep the in-memory map current so a second company later in
            # the same run that matches this one is skipped, not re-inserted.
            for key in keys:
                postgres_key_to_id[key] = new_id
        except Exception:
            logger.exception(
                'mlead: failed to persist accepted lead \'%s\' to PostgreSQL (public."TLead")',
                company.company_name,
            )

    def _backfill_mlead(self, lead_id: int, company: Company) -> None:
        """Fills in any blank column on an EXISTING mlead row with this
        company's freshly found data (GST, turnover, contact info, ...),
        without ever overwriting a column the row already has - called
        from execute()'s dedup check for a company that matches the
        baseline but may still have new data worth saving.

        Never raises - a persistence failure must not fail an otherwise
        successful validation/export run, but is always logged clearly.
        """
        try:
            _mlead_repository.backfill_blank_fields(
                lead_id,
                gst=company.gst,
                turnover=turnover_to_crore(company.turnover),
                website_url=company.website,
                mobile_number=company.phone,
                alternate_mobile_number=company.phone_alt,
                email_id=company.email,
                city=company.city,
                address=company.address,
                industry_type=company.industry,
                region=company.region,
                contact_person=company.contact_person,
                designation=company.designation,
                remarks=company.remarks,
            )
        except Exception:
            logger.exception(
                "mlead: failed to backfill existing lead_id=%s for '%s'",
                lead_id, company.company_name,
            )

    def _load_existing_keys(self, path: Path) -> set[str]:
        if not path.exists():
            return set()
        files = [path] if path.is_file() else [*path.rglob("*.csv"), *path.rglob("*.xlsx")]
        keys: set[str] = set()
        for file_path in files:
            for company in self._read_existing_companies(file_path):
                keys.update(self._company_keys(company))
        return keys

    def _read_existing_companies(self, path: Path) -> Iterator[Company]:
        if path.suffix.lower() == ".csv":
            for encoding in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    with path.open("r", encoding=encoding, newline="") as handle:
                        rows = list(csv.DictReader(handle))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return
            for row in rows:
                yield self._company_from_row(row)
            return

        if path.suffix.lower() != ".xlsx":
            return
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("Excel validation requires openpyxl; install dependencies first.") from exc

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, ())
            for row in rows:
                yield self._company_from_row({str(headers[i] or ""): row[i] for i in range(min(len(headers), len(row)))})
        finally:
            workbook.close()

    def _company_from_row(self, row: dict) -> Company:
        values = {re.sub(r"[^a-z0-9]", "", str(key).lower()): str(value).strip() if value is not None else "" for key, value in row.items()}
        def get(*names: str) -> Optional[str]:
            return next((values[name] for name in names if values.get(name)), None)
        return Company(
            company_name=get("companyname", "company", "name") or "",
            gst=get("gst", "gstin"), city=get("city"),
            website=get("websiteurl", "website", "domain"),
            email=get("emailid", "email"),
            phone=get("mobilenumber", "contactnumber", "phone", "mobile"),
            phone_alt=get("alternatemobilenumber", "alternatephone", "altphone"),
        )
