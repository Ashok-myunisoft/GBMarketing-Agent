from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from core.config import settings


class LLMTemporarilyUnavailableError(RuntimeError):
    """The upstream model provider did not recover after bounded retries."""


class LLMService:
    """
    Centralized service for communicating with the LLM.
    """

    def __init__(self):

        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not found in .env")

        # Initialize OpenAI client
        # The SDK retries transient connection, timeout, 408, 409, 429 and 5xx
        # failures. A finite limit keeps a single API request from waiting forever.
        self.client = OpenAI(api_key=settings.OPENAI_API_KEY, max_retries=4, timeout=30.0)

        # Default model
        self.model = settings.OPENAI_MODEL

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
    ) -> str:
        """
        Calls the LLM and returns the response text.
        """

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": user_prompt
                    }
                ]
            )

            return response.choices[0].message.content

        except (APIConnectionError, APITimeoutError) as e:
            raise LLMTemporarilyUnavailableError(
                "The OpenAI service could not be reached. Please retry shortly."
            ) from e
        except APIStatusError as e:
            if e.status_code >= 500 or e.status_code in (408, 409, 429):
                raise LLMTemporarilyUnavailableError(
                    "The OpenAI service is temporarily unavailable. Please retry shortly."
                ) from e
            raise RuntimeError(f"LLM Error: {str(e)}") from e
        except Exception as e:
            raise RuntimeError(f"LLM Error: {str(e)}") from e
