import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR.parent / ".env")


class Settings:

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
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
