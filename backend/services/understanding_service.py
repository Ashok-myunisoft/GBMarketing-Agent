import json
from pydantic import ValidationError
from services.prompt_service import PromptService
from services.llm_services import LLMService
from schemas.query_understanding import QueryUnderstanding




class UnderstandingService:

    def __init__(self):

        self.prompt_service = PromptService()

        self.llm = LLMService()

    def understand(self, query: str):

        system_prompt = self.prompt_service.load("query_understanding")

        response = self.llm.invoke(

            system_prompt=system_prompt,

            user_prompt=query

        )

        try:
            data = json.loads(response)
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM did not return valid JSON: {response!r}") from e

        try:
            return QueryUnderstanding(**data)
        except ValidationError as e:
            raise ValueError(f"LLM JSON did not match expected schema: {e}") from e