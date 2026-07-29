from agents.base_agent import BaseClass
from orchestrator.workflow import WorkflowOrchestrator
from models.workflow_context import WorkflowContext
from schemas.query_understanding import QueryUnderstanding


class PlannerAgent(BaseClass):

    _PLACEHOLDER_VALUES = {"", "n/a", "na", "none", "null", "not available", "unknown"}

    def __init__(self, progress_callback=None):
        self._progress_callback = progress_callback

    @classmethod
    def _optional_value(cls, value: str | None) -> str | None:
        """Converts LLM placeholder strings into actual missing values."""
        cleaned = (value or "").strip()
        return None if cleaned.lower() in cls._PLACEHOLDER_VALUES else cleaned

    def execute(
        self,
        understanding: QueryUnderstanding,
        user_query: str = "",
    ):

        print("========== Planner Agent Started ==========")

        # Query understanding is best-effort LLM output. This application has
        # one operational workflow for company/industry listing requests, so
        # never let a missing optional LLM field turn a valid search into a
        # successful no-op.
        workflow = self._optional_value(understanding.workflow) or "lead_generation"

        # Build the workflow context
        context = WorkflowContext(
            user_query=user_query,
            intent=self._optional_value(understanding.intent),
            workflow=workflow,
            industry=self._optional_value(understanding.industry),
            location=self._optional_value(understanding.location),
            buyer_persona=self._optional_value(understanding.buyer_persona),
            confidence=understanding.confidence,
        )

        # Execute the workflow
        orchestrator = WorkflowOrchestrator(progress_callback=self._progress_callback)
        orchestrator.execute(context)

        print("========== Planner Agent Completed ==========")

        return context
