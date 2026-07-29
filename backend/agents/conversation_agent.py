from agents.base_agent import BaseClass
from agents.planner_agent import PlannerAgent
from agents.query_understanding_agent import QueryUnderstandingAgent


class ConversationAgent(BaseClass):

    def __init__(self, progress_callback=None):
        self.query_agent = QueryUnderstandingAgent()
        self.planner = PlannerAgent(progress_callback=progress_callback)

    def execute(self, message: str):

        print("========== Conversation Agent Started ==========")

        # Step 1: Understand the user's query
        understanding = self.query_agent.execute(message)

        print("Query Understanding Completed")
        print(understanding)

        # Step 2: Create workflow context and start workflow
        context = self.planner.execute(understanding, user_query=message)

        print("Conversation Agent Completed")

        return context
