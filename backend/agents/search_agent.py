from typing import List

from agents.base_agent import BaseClass
from schemas.search_request import SearchRequest
from schemas.company import Company
from services.search_services import SearchService


class SearchAgent(BaseClass):

    def __init__(self):
        self.search_service = SearchService()

    def execute(self, request: SearchRequest) -> List[Company]:

        print("========== Search Agent Started ==========")

        if not isinstance(request, SearchRequest):
            raise TypeError(
                f"SearchAgent.execute expects SearchRequest, got {type(request).__name__}"
            )

        print(f"Industry    : {request.industry}")
        print(f"Location    : {request.location}")
        print(f"Max Results : {request.max_results}")

        leads = self.search_service.search(request)

        print(f"Leads Found : {len(leads)}")

        print("========== Search Agent Completed ==========")

        return leads
