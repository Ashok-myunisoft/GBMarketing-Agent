import logging
import time

from orchestrator.workflow_registry import WORKFLOWS
from config.geography import classify_location
from models.workflow_context import PipelineStats, RemovedCompany, WorkflowContext
from schemas.search_request import SearchRequest
from agents.search_agent import SearchAgent
from agents.enrichment_agent import EnrichmentAgent
from agents.validation_agent import ValidationAgent
from agents.export_agent import ExportAgent
from services.company_identity import is_generic_maps_label

logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """
    Drives a WorkflowContext through the registered steps for its
    workflow. Each step name is mapped to a handler; steps without a
    handler yet fall back to a print-only stub. Adding a new agent means
    adding one entry to _step_handlers, not touching the dispatch loop.
    """

    def __init__(self, progress_callback=None):
        self._progress_callback = progress_callback
        self._step_handlers = {
            "search": self._run_search,
            "enrichment": self._run_enrichment,
            "validation": self._run_validation,
            "contact": self._run_contact,
            "export": self._run_export,
        }

    def _emit_progress(self, step: str, status: str, message: str) -> None:
        if self._progress_callback:
            self._progress_callback(step, status, message)

    def execute(self, context: WorkflowContext) -> WorkflowContext:

        print("\n========== Workflow Started ==========\n")

        print(f"User Query   : {context.user_query}")
        print(f"Intent       : {context.intent}")
        print(f"Workflow     : {context.workflow}")
        print(f"Industry     : {context.industry}")
        print(f"Location     : {context.location}")
        print(f"BuyerPersona : {context.buyer_persona}")
        print(f"Confidence   : {context.confidence}")

        print("\n--------------------------------------")

        steps = WORKFLOWS.get(context.workflow, [])

        if not steps:
            print(f"No workflow found for '{context.workflow}'")
            return context

        step_index = 0
        while step_index < len(steps):
            step = steps[step_index]
            if (
                step == "enrichment"
                and step_index + 1 < len(steps)
                and steps[step_index + 1] == "validation"
            ):
                try:
                    context = self._run_enrichment_validation_batches(context)
                except Exception as exc:
                    self._emit_progress("enrichment", "failed", f"Enrichment failed: {exc}")
                    self._emit_progress("validation", "failed", f"Validation failed: {exc}")
                    raise
                step_index += 2
                continue

            handler = self._step_handlers.get(step)

            if handler:
                self._emit_progress(step, "running", f"{step.title()} started")
                step_started = time.monotonic()
                try:
                    context = handler(context)
                except Exception as exc:
                    logger.exception("[PERF] workflow step failed step=%s", step)
                    self._emit_progress(step, "failed", f"{step.title()} failed: {exc}")
                    raise
                elapsed_ms = round((time.monotonic() - step_started) * 1000)
                logger.info("[PERF] workflow_step step=%s elapsed_ms=%d", step, elapsed_ms)
                self._emit_progress(step, "completed", f"{step.title()} completed in {elapsed_ms / 1000:.1f}s")
            else:
                print(f"Executing -> {step}")

            step_index += 1

        print("\n========== Workflow Completed ==========\n")

        return context

    def _run_search(self, context: WorkflowContext) -> WorkflowContext:

        print("Executing -> search")
        started = time.monotonic()

        search_request = self._build_search_request(context)

        search_agent = SearchAgent()

        context.companies = search_agent.execute(search_request)
        logger.info(
            "[PERF] workflow search elapsed_ms=%d candidates=%d query=%r",
            round((time.monotonic() - started) * 1000),
            len(context.companies),
            context.user_query,
        )

        stats = getattr(search_agent, "last_run_stats", None) or {}
        context.pipeline_stats = PipelineStats(
            raw_fetched=stats.get("raw_fetched", len(context.companies)),
            after_search_dedup=stats.get("after_dedup", len(context.companies)),
            after_enrichment=len(context.companies),
            after_validation=len(context.companies),
            removed=[
                RemovedCompany(
                    company_name=item["company_name"],
                    stage=item.get("stage", "search_dedup"),
                    reason=item["reason"],
                    source_table=item.get("source_table"),
                    source_row_id=item.get("source_row_id"),
                    matched_identity_key=item.get("matched_identity_key"),
                    matched_source=item.get("matched_source"),
                    matched_record_id=item.get("matched_record_id"),
                    source_row_ids=item.get("source_row_ids", []),
                    occurrence_count=item.get("occurrence_count", 1),
                )
                for item in stats.get("removed", [])
            ],
        )

        return context

    def _build_search_request(self, context: WorkflowContext) -> SearchRequest:

        keywords = [keyword for keyword in [context.buyer_persona] if keyword]

        return SearchRequest(
            industry=context.industry,
            location=context.location,
            keywords=keywords,
            max_results=context.requested_company_count or SearchRequest.model_fields["max_results"].default,
        )

    def _run_enrichment(self, context: WorkflowContext) -> WorkflowContext:

        print("Executing -> enrichment")

        enrichment_agent = EnrichmentAgent()

        context.companies = enrichment_agent.execute(context.companies)

        # Restore the database name if a scraper returns the generic Maps page
        # title. Only discard a row when its source name is generic too.
        retained = []
        generic_labels = []
        for company in context.companies:
            if is_generic_maps_label(company.company_name):
                source_name = (company.source_metadata or {}).get("_source_company_name")
                if source_name and not is_generic_maps_label(str(source_name)):
                    retained.append(company.model_copy(update={"company_name": str(source_name)}))
                else:
                    generic_labels.append(company)
            else:
                retained.append(company)
        context.companies = retained
        if context.pipeline_stats and generic_labels:
            context.pipeline_stats.removed.extend(
                RemovedCompany(
                    company_name=company.company_name or "(unnamed)",
                    stage="search_filter",
                    reason=(
                        "removed generic Google Maps label after enrichment; "
                        f"source company name: {company.source_metadata.get('_source_company_name') or '(not recorded)'}"
                    ),
                    source_table=company.source_table,
                    source_row_id=company.source_row_id,
                )
                for company in generic_labels
            )

        if context.pipeline_stats:
            context.pipeline_stats.after_enrichment = len(context.companies)
            context.pipeline_stats.after_validation = len(context.companies)

        return context

    def _run_validation(self, context: WorkflowContext) -> WorkflowContext:
        print("Executing -> validation")
        validation_agent = ValidationAgent()
        context.companies = validation_agent.execute(
            context.companies,
            existing_excel_path=context.existing_excel_path,
            requested_location=context.location,
            requested_industry=context.industry,
            requested_turnover_floor_cr=context.requested_turnover_floor_cr,
            requested_employee_floor=context.requested_employee_floor,
            requested_gst_required=context.requested_gst_required,
        )
        context.validation_stats = getattr(validation_agent, "last_run_stats", None)
        if context.pipeline_stats:
            context.pipeline_stats.after_validation = len(context.companies)
            context.pipeline_stats.removed = context.pipeline_stats.removed + [
                RemovedCompany(
                    company_name=item["company_name"],
                    stage="validation",
                    reason=item["reason"],
                    source_table=item.get("source_table"),
                    source_row_id=item.get("source_row_id"),
                    matched_identity_key=item.get("matched_identity_key"),
                    matched_source=item.get("matched_source"),
                    matched_record_id=item.get("matched_record_id"),
                    matched_baselines=item.get("matched_baselines", []),
                    source_row_ids=item.get("source_row_ids", []),
                    occurrence_count=item.get("occurrence_count", 1),
                )
                for item in getattr(validation_agent, "removed_companies", [])
            ]
        return context

    def _run_enrichment_validation_batches(self, context: WorkflowContext) -> WorkflowContext:
        """Process unique source companies until the requested number pass validation.

        Source duplicates are collapsed by the search service before this
        point. Batching prevents enriching every matching database row, while
        continuing past candidates rejected by the unchanged validation rules.
        """
        candidates = context.companies
        target = max(
            1,
            context.requested_company_count
            or SearchRequest.model_fields["max_results"].default,
        )
        batch_size = min(30, target)
        accepted = []
        processed_for_validation = 0
        aggregate_validation_stats = {
            "scanned": 0,
            "new": 0,
            "duplicates": 0,
            "rejected": 0,
            "out_of_area": 0,
            "location_unknown": 0,
            "no_identifiers": 0,
        }

        if context.pipeline_stats:
            context.pipeline_stats.after_enrichment = 0
            context.pipeline_stats.after_validation = 0

        for start in range(0, len(candidates), batch_size):
            if len(accepted) >= target:
                break
            batch_number = start // batch_size + 1
            batch = candidates[start:start + batch_size]

            # Discovery DB results are already scoped by the exact `locations`
            # array match. Still reject candidates whose own stored city,
            # state, address, or locality positively conflicts with the query
            # before spending time on GST/turnover enrichment and geocoding.
            # Unknown geography stays in the batch; only confirmed conflicts
            # are removed here, matching ValidationAgent's existing policy.
            enrichment_batch = []
            for company in batch:
                if company.source_table != "public.discovered_companies" or not context.location:
                    enrichment_batch.append(company)
                    continue
                decision, reason = classify_location(
                    company.city,
                    company.state,
                    company.address,
                    context.location,
                    company_district=company.district,
                    company_locality=company.locality,
                )
                if decision != "outside":
                    enrichment_batch.append(company)
                    continue

                logger.info(
                    "[LOCATION] pre_enrichment_reject requested=%s company=%s source_row_id=%s "
                    "source_locations=%s company_locality=%s resolved_city=%s resolved_state=%s reason=%s",
                    context.location,
                    company.company_name,
                    company.source_row_id,
                    (company.source_metadata or {}).get("_source_locations"),
                    company.locality,
                    company.city,
                    company.state,
                    reason,
                )
                aggregate_validation_stats["scanned"] += 1
                aggregate_validation_stats["out_of_area"] += 1
                processed_for_validation += 1
                if context.pipeline_stats:
                    context.pipeline_stats.removed.append(RemovedCompany(
                        company_name=company.company_name or "(unnamed)",
                        stage="validation",
                        reason=f"located outside requested area '{context.location}' ({reason})",
                        source_table=company.source_table,
                        source_row_id=company.source_row_id,
                    ))

            if not enrichment_batch:
                self._emit_progress(
                    "enrichment", "completed",
                    f"Skipped batch {batch_number}; all {len(batch)} candidates were confirmed outside the requested area",
                )
                self._emit_progress(
                    "validation", "completed",
                    f"Batch {batch_number}: rejected {len(batch)} out-of-area candidates before enrichment",
                )
                logger.info(
                    "[PERF] workflow batch skipped batch=%d candidates=%d reason=confirmed_out_of_area",
                    batch_number, len(batch),
                )
                continue

            self._emit_progress(
                "enrichment", "running",
                f"Enriching company batch {batch_number} ({len(enrichment_batch)} companies)",
            )
            enrichment_started = time.monotonic()
            enriched = EnrichmentAgent().execute(enrichment_batch)
            enrichment_elapsed_ms = round((time.monotonic() - enrichment_started) * 1000)
            logger.info(
                "[PERF] workflow enrichment_batch batch=%d candidates=%d elapsed_ms=%d",
                batch_number, len(enrichment_batch), enrichment_elapsed_ms,
            )

            usable = []
            generic_labels = []
            for company in enriched:
                if not is_generic_maps_label(company.company_name):
                    usable.append(company)
                    continue
                source_name = (company.source_metadata or {}).get("_source_company_name")
                if source_name and not is_generic_maps_label(str(source_name)):
                    usable.append(company.model_copy(update={"company_name": str(source_name)}))
                else:
                    generic_labels.append(company)

            if context.pipeline_stats:
                context.pipeline_stats.after_enrichment += len(usable)
                context.pipeline_stats.removed.extend(
                    RemovedCompany(
                        company_name=company.company_name or "(unnamed)",
                        stage="search_filter",
                        reason=(
                            "removed generic Google Maps label after enrichment; "
                            f"source company name: {(company.source_metadata or {}).get('_source_company_name') or '(not recorded)'}"
                        ),
                        source_table=company.source_table,
                        source_row_id=company.source_row_id,
                    )
                    for company in generic_labels
                )
            self._emit_progress(
                "enrichment", "completed",
                f"Enrichment batch {batch_number} completed in {enrichment_elapsed_ms / 1000:.1f}s",
            )

            remaining = target - len(accepted)
            validation_agent = ValidationAgent()
            self._emit_progress(
                "validation", "running",
                f"Validating company batch {batch_number}; {remaining} more new leads requested",
            )
            validation_started = time.monotonic()
            newly_accepted = validation_agent.execute(
                usable,
                existing_excel_path=context.existing_excel_path,
                requested_location=context.location,
                requested_industry=context.industry,
                requested_turnover_floor_cr=context.requested_turnover_floor_cr,
                requested_employee_floor=context.requested_employee_floor,
                requested_gst_required=context.requested_gst_required,
                max_new_companies=remaining,
            )
            validation_elapsed_ms = round((time.monotonic() - validation_started) * 1000)
            logger.info(
                "[PERF] workflow validation_batch batch=%d candidates=%d accepted=%d elapsed_ms=%d",
                batch_number,
                len(usable),
                len(newly_accepted),
                validation_elapsed_ms,
            )
            accepted.extend(newly_accepted)
            validation_stats = getattr(validation_agent, "last_run_stats", None) or {}
            for key in aggregate_validation_stats:
                aggregate_validation_stats[key] += int(validation_stats.get(key, 0) or 0)
            processed_for_validation += int(validation_stats.get("scanned", 0) or 0)

            if context.pipeline_stats:
                context.pipeline_stats.after_validation = len(accepted)
                context.pipeline_stats.removed.extend(
                    RemovedCompany(
                        company_name=item["company_name"],
                        stage="validation",
                        reason=item["reason"],
                        source_table=item.get("source_table"),
                        source_row_id=item.get("source_row_id"),
                        matched_identity_key=item.get("matched_identity_key"),
                        matched_source=item.get("matched_source"),
                        matched_record_id=item.get("matched_record_id"),
                        matched_baselines=item.get("matched_baselines", []),
                        source_row_ids=item.get("source_row_ids", []),
                        occurrence_count=item.get("occurrence_count", 1),
                    )
                    for item in getattr(validation_agent, "removed_companies", [])
                )
            self._emit_progress(
                "validation", "completed",
                f"Validation batch {batch_number} completed in {validation_elapsed_ms / 1000:.1f}s; "
                f"{len(accepted)} of {target} new leads accepted",
            )

        context.companies = accepted[:target]
        context.validation_stats = aggregate_validation_stats
        if context.pipeline_stats:
            context.pipeline_stats.after_validation = len(context.companies)
        if not candidates:
            self._emit_progress("enrichment", "completed", "No company candidates to enrich")
            self._emit_progress("validation", "completed", "No company candidates to validate")
        print(
            f"Accepted {len(context.companies)} of requested {target} new leads "
            f"after processing {processed_for_validation} validation candidates."
        )
        return context

    def _run_contact(self, context: WorkflowContext) -> WorkflowContext:
        # Contact discovery is intentionally folded into EnrichmentAgent so it
        # can use the same website visit rather than issuing another crawl.
        print("Executing -> contact (included in enrichment)")
        return context

    def _run_export(self, context: WorkflowContext) -> WorkflowContext:
        print("Executing -> export")
        context.export_path = ExportAgent().execute(context.companies, context.export_path)
        return context

    
