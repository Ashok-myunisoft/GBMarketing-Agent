import unittest
from unittest.mock import patch

from agents.planner_agent import PlannerAgent
from schemas.query_understanding import QueryUnderstanding


class PlannerAgentTests(unittest.TestCase):
    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_unknown_workflow_falls_back_to_lead_generation_and_recovers_industry(self, orchestrator):
        understanding = QueryUnderstanding(
            intent="list_industries",
            industry="",
            location="Hyderabad",
            workflow="list_industries",
            confidence=0.95,
        )

        context = PlannerAgent().execute(
            understanding, user_query="list all valve industries in hyderabad"
        )

        self.assertEqual(context.workflow, "lead_generation")
        self.assertEqual(context.industry, "Valve")
        orchestrator.return_value.execute.assert_called_once_with(context)

    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_explicit_company_count_from_the_query_is_carried_to_the_context(self, orchestrator):
        understanding = QueryUnderstanding(
            intent="lead_generation", industry="Valve", location="Hyderabad",
            workflow="lead_generation", confidence=0.95,
        )

        context = PlannerAgent().execute(understanding, user_query="find 20 valve companies in Hyderabad")

        self.assertEqual(context.requested_company_count, 20)

    def _understanding(self):
        return QueryUnderstanding(
            intent="lead_generation", industry="Valve", location="Gujarat",
            workflow="lead_generation", confidence=0.95,
        )

    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_no_explicit_filters_by_default(self, orchestrator):
        context = PlannerAgent().execute(self._understanding(), user_query="valve companies in Gujarat")

        self.assertIsNone(context.requested_turnover_floor_cr)
        self.assertIsNone(context.requested_employee_floor)
        self.assertFalse(context.requested_gst_required)

    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_explicit_turnover_filter_is_detected(self, orchestrator):
        context = PlannerAgent().execute(
            self._understanding(),
            user_query="valve companies in Gujarat with turnover above 50 crore",
        )

        self.assertEqual(context.requested_turnover_floor_cr, 50.0)

    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_explicit_employee_filter_is_detected(self, orchestrator):
        context = PlannerAgent().execute(
            self._understanding(),
            user_query="valve companies in Gujarat with 100+ employees",
        )

        self.assertEqual(context.requested_employee_floor, 100.0)

    @patch("agents.planner_agent.WorkflowOrchestrator")
    def test_explicit_gst_requirement_is_detected(self, orchestrator):
        context = PlannerAgent().execute(
            self._understanding(),
            user_query="valve companies in Gujarat with GST number",
        )

        self.assertTrue(context.requested_gst_required)


if __name__ == "__main__":
    unittest.main()
