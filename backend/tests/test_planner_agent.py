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


if __name__ == "__main__":
    unittest.main()
