from orchestrator.workflow_registry import WORKFLOWS
from models.workflow_context import WorkflowContext
from schemas.search_request import SearchRequest
from agents.search_agent import SearchAgent
from agents.enrichment_agent import EnrichmentAgent
from agents.validation_agent import ValidationAgent
from agents.export_agent import ExportAgent


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

        for step in steps:
            handler = self._step_handlers.get(step)

            if handler:
                self._emit_progress(step, "running", f"{step.title()} started")
                try:
                    context = handler(context)
                except Exception as exc:
                    self._emit_progress(step, "failed", f"{step.title()} failed: {exc}")
                    raise
                self._emit_progress(step, "completed", f"{step.title()} completed")
            else:
                print(f"Executing -> {step}")

        print("\n========== Workflow Completed ==========\n")

        return context

    def _run_search(self, context: WorkflowContext) -> WorkflowContext:

        print("Executing -> search")

        search_request = self._build_search_request(context)

        search_agent = SearchAgent()

        context.companies = search_agent.execute(search_request)

        return context

    def _build_search_request(self, context: WorkflowContext) -> SearchRequest:

        keywords = [keyword for keyword in [context.buyer_persona] if keyword]

        return SearchRequest(
            industry=context.industry,
            location=context.location,
            keywords=keywords,
        )

    def _run_enrichment(self, context: WorkflowContext) -> WorkflowContext:

        print("Executing -> enrichment")

        enrichment_agent = EnrichmentAgent()

        context.companies = enrichment_agent.execute(context.companies)

        return context

    def _run_validation(self, context: WorkflowContext) -> WorkflowContext:
        print("Executing -> validation")
        context.companies = ValidationAgent().execute(
            context.companies,
            existing_excel_path=context.existing_excel_path,
            requested_location=context.location,
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

    
