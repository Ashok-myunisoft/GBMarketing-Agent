from agents.base_agent import BaseClass
from services.understanding_service import UnderstandingService


class QueryUnderstandingAgent(BaseClass):

    def __init__(self):

        self.service = UnderstandingService()

    def execute(self, query: str):

        return self.service.understand(query)