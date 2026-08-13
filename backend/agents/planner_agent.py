from agents.base_agent import BaseClass
import re

from orchestrator.workflow import WorkflowOrchestrator
from orchestrator.workflow_registry import WORKFLOWS
from config.targeting import match_target_industry
from config.geography import canonical_city
from models.workflow_context import WorkflowContext
from schemas.query_understanding import QueryUnderstanding


class PlannerAgent(BaseClass):

    _PLACEHOLDER_VALUES = {
        "",
        "n/a",
        "na",
        "none",
        "null",
        "not available",
        "unknown",
    }

    _COMPANY_COUNT_PATTERN = re.compile(
        r"\b(\d{1,6})\b"
        r"(?:\s+[a-z][a-z&/-]*){0,3}\s+"
        r"(?:companies|company|leads|lead|businesses|business)\b",
        re.IGNORECASE,
    )

    _MAX_SEARCH_RESULTS = 100_000

    # These detect an EXPLICIT size/registration filter in the raw query.
    # ValidationAgent should only enforce these filters when the user
    # actually requested them.
    _COMPARISON_WORDS = (
        r"(?:above|over|more than|greater than|at least|minimum|min)"
    )

    _TURNOVER_FLOOR_PATTERN = re.compile(
        r"\b(?:turnover|revenue)\b"
        r"[^.\n]{0,20}?"
        r"\b"
        + _COMPARISON_WORDS
        + r"\b"
        r"[^.\n]{0,10}?"
        r"(\d+(?:\.\d+)?)\s*(?:cr|crore)\b",
        re.IGNORECASE,
    )

    _EMPLOYEE_FLOOR_PATTERNS = (
        re.compile(
            r"\b(\d+(?:\.\d+)?)\s*\+\s*"
            r"(?:employees|staff|headcount)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:employees|staff|headcount)\b"
            r"[^.\n]{0,20}?"
            r"\b"
            + _COMPARISON_WORDS
            + r"\b"
            r"[^.\n]{0,10}?"
            r"(\d+(?:\.\d+)?)\b",
            re.IGNORECASE,
        ),
    )

    _GST_REQUIRED_PATTERN = re.compile(
        r"\b(?:with|having|valid|registered)\b"
        r"[^.\n]{0,15}?\bgst\b"
        r"|"
        r"\bgst\b"
        r"[^.\n]{0,15}?"
        r"\b(?:registered|required|mandatory)\b",
        re.IGNORECASE,
    )

    def __init__(self, progress_callback=None):
        self._progress_callback = progress_callback

    @classmethod
    def _optional_value(cls, value: str | None) -> str | None:
        """
        Converts LLM placeholder strings into actual missing values.
        """
        cleaned = (value or "").strip()

        if cleaned.lower() in cls._PLACEHOLDER_VALUES:
            return None

        return cleaned

    @classmethod
    def _requested_company_count(
        cls,
        query: str,
        llm_count: int | None,
    ) -> int | None:
        """
        Prefer the explicit number in the raw request over LLM output.
        """
        match = cls._COMPANY_COUNT_PATTERN.search(query or "")

        requested = int(match.group(1)) if match else llm_count

        if requested is None or requested <= 0:
            return None

        return min(requested, cls._MAX_SEARCH_RESULTS)

    @classmethod
    def _requested_turnover_floor_cr(
        cls,
        query: str,
    ) -> float | None:
        """
        Returns the turnover floor in Cr only when the query explicitly
        asks for one.
        """
        match = cls._TURNOVER_FLOOR_PATTERN.search(query or "")

        if not match:
            return None

        return float(match.group(1))

    @classmethod
    def _requested_employee_floor(
        cls,
        query: str,
    ) -> float | None:
        """
        Returns the headcount floor only when the query explicitly asks
        for one.
        """
        for pattern in cls._EMPLOYEE_FLOOR_PATTERNS:
            match = pattern.search(query or "")

            if match:
                return float(match.group(1))

        return None

    @classmethod
    def _requested_gst_required(
        cls,
        query: str,
    ) -> bool:
        """
        True only when the query explicitly requires a GST number,
        not merely because the query mentions GST.
        """
        return bool(cls._GST_REQUIRED_PATTERN.search(query or ""))

    @classmethod
    def _resolve_industry(
        cls,
        understanding: QueryUnderstanding,
        user_query: str,
    ) -> str | None:
        """
        Resolve the industry without allowing the generic deterministic
        'Manufacturing' bucket to overwrite a more useful LLM value.

        Priority:

        1. Specific deterministic match from the raw user query
        2. LLM industry value
        3. None

        Important:
        If match_target_industry() returns only the generic
        'Manufacturing' bucket, we DO NOT overwrite the LLM's industry.

        Example:

            user_query = "textile machinery companies"
            LLM industry = "Textile Machinery"
            deterministic match = "Manufacturing"

        Result:

            industry = "Textile Machinery"

        But:

            user_query = "valve companies"
            deterministic match = "Valve"

        Result:

            industry = "Valve"
        """

        llm_industry = cls._optional_value(understanding.industry)

        deterministic_match = match_target_industry(user_query)

        # Only allow deterministic matching to override the LLM when
        # it found a specific catalogued segment.
        if (
            deterministic_match
            and deterministic_match.strip().lower() != "manufacturing"
        ):
            print(
                "[PLANNER][INDUSTRY] "
                f"Specific deterministic match: {deterministic_match!r}"
            )

            return deterministic_match

        # Generic "Manufacturing" must NOT replace the LLM's actual text.
        if deterministic_match == "Manufacturing":
            print(
                "[PLANNER][INDUSTRY] "
                "Deterministic match is generic 'Manufacturing'; "
                f"keeping LLM industry: {llm_industry!r}"
            )

        return llm_industry

    def execute(
        self,
        understanding: QueryUnderstanding,
        user_query: str = "",
    ):

        print("========== Planner Agent Started ==========")

        # ------------------------------------------------------------
        # WORKFLOW
        # ------------------------------------------------------------

        requested_workflow = self._optional_value(
            understanding.workflow
        )

        workflow = (
            requested_workflow
            if requested_workflow in WORKFLOWS
            else "lead_generation"
        )

        # ------------------------------------------------------------
        # INDUSTRY
        # ------------------------------------------------------------

        industry = self._resolve_industry(
            understanding=understanding,
            user_query=user_query,
        )

        print(
            "[PLANNER] Final industry: "
            f"{industry!r}"
        )

        # ------------------------------------------------------------
        # LOCATION
        # ------------------------------------------------------------

        location = self._optional_value(
            understanding.location
        )

        deterministic_city = canonical_city(
            user_query
        )

        if deterministic_city:
            location = deterministic_city

            print(
                "[PLANNER][LOCATION] "
                f"Deterministic location: {location!r}"
            )

        # ------------------------------------------------------------
        # COMPANY COUNT
        # ------------------------------------------------------------

        requested_company_count = self._requested_company_count(
            user_query,
            understanding.requested_company_count,
        )

        # ------------------------------------------------------------
        # BUILD WORKFLOW CONTEXT
        # ------------------------------------------------------------

        context = WorkflowContext(
            user_query=user_query,
            intent=self._optional_value(
                understanding.intent
            ),
            workflow=workflow,
            industry=industry,
            location=location,
            buyer_persona=self._optional_value(
                understanding.buyer_persona
            ),
            confidence=understanding.confidence,
            requested_company_count=requested_company_count,
            requested_turnover_floor_cr=(
                self._requested_turnover_floor_cr(
                    user_query
                )
            ),
            requested_employee_floor=(
                self._requested_employee_floor(
                    user_query
                )
            ),
            requested_gst_required=(
                self._requested_gst_required(
                    user_query
                )
            ),
        )

        print(
            "[PLANNER] Workflow:",
            workflow,
        )

        print(
            "[PLANNER] Industry:",
            industry,
        )

        print(
            "[PLANNER] Location:",
            location,
        )

        print(
            "[PLANNER] Requested company count:",
            requested_company_count,
        )

        print(
            "[PLANNER] Requested turnover floor:",
            context.requested_turnover_floor_cr,
        )

        print(
            "[PLANNER] Requested employee floor:",
            context.requested_employee_floor,
        )

        print(
            "[PLANNER] GST required:",
            context.requested_gst_required,
        )

        # ------------------------------------------------------------
        # EXECUTE WORKFLOW
        # ------------------------------------------------------------

        orchestrator = WorkflowOrchestrator(
            progress_callback=self._progress_callback
        )

        orchestrator.execute(context)

        print("========== Planner Agent Completed ==========")

        return context

    