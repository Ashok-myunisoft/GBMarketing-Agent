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
    # Optional credentials for the LinkedIn contact-enrichment fallback.
    # Keep these in the environment; never add them to source control.
    LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL")
    LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")

    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))
    # Enrichment uses independent browser sessions in a small worker pool.  A
    # conservative default keeps third-party sites and APIs from being flooded.
    ENRICHMENT_CONCURRENCY = max(1, int(os.getenv("ENRICHMENT_CONCURRENCY", "3")))
    ENRICHMENT_MAX_SUPPLEMENTAL_PAGES = max(
        0, int(os.getenv("ENRICHMENT_MAX_SUPPLEMENTAL_PAGES", "2"))
    )
    ENRICHMENT_PLAYWRIGHT_TIMEOUT_MS = max(
        1_000, int(os.getenv("ENRICHMENT_PLAYWRIGHT_TIMEOUT_MS", "15000"))
    )
    # LinkedIn is comparatively slow and has low coverage for the
    # industrial-company searches this app targets, so it stays an explicit
    # opt-in for a deep-enrichment run. GST + turnover extraction (Google
    # search -> jamku.app) is core to every run, so it defaults on.
    ENRICHMENT_LOOKUP_LINKEDIN = os.getenv("ENRICHMENT_LOOKUP_LINKEDIN", "false").lower() == "true"
    ENRICHMENT_LOOKUP_TURNOVER = os.getenv("ENRICHMENT_LOOKUP_TURNOVER", "true").lower() == "true"


settings = Settings()
