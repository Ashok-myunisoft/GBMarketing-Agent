import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR.parent / ".env")


class Settings:

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_API_BASE_URL = os.getenv("OPENAI_API_BASE_URL")
    # Timeout for HTTP calls to the LLM provider (seconds)
    OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
    # Polling configuration for Runpod-style POST -> GET flows
    RUNPOD_POLL_ATTEMPTS = int(os.getenv("RUNPOD_POLL_ATTEMPTS", "12"))
    RUNPOD_POLL_BACKOFF_SECONDS = float(os.getenv("RUNPOD_POLL_BACKOFF_SECONDS", "1.0"))
    RUNPOD_POLL_MAX_DELAY_SECONDS = float(os.getenv("RUNPOD_POLL_MAX_DELAY_SECONDS", "8.0"))
    RUNPOD_POLL_TIMEOUT_SECONDS = float(os.getenv("RUNPOD_POLL_TIMEOUT_SECONDS", "120.0"))
    # Most custom RunPod workers accept a single prompt. Set to "messages" only
    # for the documented RunPod vLLM chat-worker contract.
    RUNPOD_INPUT_MODE = os.getenv("RUNPOD_INPUT_MODE", "prompt").lower()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY")
    FILESURE_API_KEY = os.getenv("FILESURE_API_KEY") or os.getenv("FILE_SURE_API_KEY")
    RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")
    # Optional credentials for the LinkedIn contact-enrichment fallback.
    # Keep these in the environment; never add them to source control.
    LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL")
    LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")

    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))


settings = Settings()
