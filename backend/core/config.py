import os
from pathlib import Path
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR.parent / ".env")


class Settings:

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_API_BASE_URL = os.getenv("OPENAI_API_BASE_URL")

    OPENAI_TIMEOUT_SECONDS = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))

    RUNPOD_POLL_ATTEMPTS = int(os.getenv("RUNPOD_POLL_ATTEMPTS", "12"))
    RUNPOD_POLL_BACKOFF_SECONDS = float(os.getenv("RUNPOD_POLL_BACKOFF_SECONDS", "1.0"))
    RUNPOD_POLL_MAX_DELAY_SECONDS = float(os.getenv("RUNPOD_POLL_MAX_DELAY_SECONDS", "8.0"))
    RUNPOD_POLL_TIMEOUT_SECONDS = float(os.getenv("RUNPOD_POLL_TIMEOUT_SECONDS", "120.0"))

    RUNPOD_INPUT_MODE = os.getenv("RUNPOD_INPUT_MODE", "prompt").lower()
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY")
    FILESURE_API_KEY = os.getenv("FILESURE_API_KEY") or os.getenv("FILE_SURE_API_KEY")

    LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL")
    LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD")

    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "30000"))

    PLAYWRIGHT_ENGINE = os.getenv("PLAYWRIGHT_ENGINE", "rotate").lower()

    ENRICHMENT_CONCURRENCY = max(1, int(os.getenv("ENRICHMENT_CONCURRENCY", "3")))
    ENRICHMENT_MAX_SUPPLEMENTAL_PAGES = max(
        0, int(os.getenv("ENRICHMENT_MAX_SUPPLEMENTAL_PAGES", "2"))
    )
    ENRICHMENT_PLAYWRIGHT_TIMEOUT_MS = max(
        1_000, int(os.getenv("ENRICHMENT_PLAYWRIGHT_TIMEOUT_MS", "15000"))
    )

    ENRICHMENT_LOOKUP_LINKEDIN = os.getenv("ENRICHMENT_LOOKUP_LINKEDIN", "false").lower() == "true"
    ENRICHMENT_LOOKUP_TURNOVER = os.getenv("ENRICHMENT_LOOKUP_TURNOVER", "true").lower() == "true"
   
    ENRICHMENT_LOOKUP_LLM_CONTACT = os.getenv("ENRICHMENT_LOOKUP_LLM_CONTACT", "false").lower() == "true"

    ENRICHMENT_GST_TURNOVER_AI_VALIDATION = os.getenv("ENRICHMENT_GST_TURNOVER_AI_VALIDATION", "true").lower() == "true"

    ENRICHMENT_CONTACT_MIN_CONFIDENCE = int(os.getenv("ENRICHMENT_CONTACT_MIN_CONFIDENCE", "50"))

  
    TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
    TAVILY_API_BASE_URL = os.getenv("TAVILY_API_BASE_URL", "https://api.tavily.com")
    TAVILY_MAX_RESULTS_PER_QUERY = max(1, int(os.getenv("TAVILY_MAX_RESULTS_PER_QUERY", "5")))
 
    TAVILY_SEARCH_CONCURRENCY = max(1, int(os.getenv("TAVILY_SEARCH_CONCURRENCY", "3")))
    TAVILY_MAX_PAGES = max(1, int(os.getenv("TAVILY_MAX_PAGES", "10")))
    TAVILY_MAX_PDFS = max(0, int(os.getenv("TAVILY_MAX_PDFS", "5")))
    TAVILY_MAX_DOCUMENT_CHARS = max(2_000, int(os.getenv("TAVILY_MAX_DOCUMENT_CHARS", "40000")))
    ENRICHMENT_USE_TAVILY_PIPELINE = os.getenv("ENRICHMENT_USE_TAVILY_PIPELINE", "true").lower() == "true"
    ENRICHMENT_TAVILY_MIN_CONFIDENCE = int(os.getenv("ENRICHMENT_TAVILY_MIN_CONFIDENCE", "80"))
    ENRICHMENT_TAVILY_MAX_RETRIES = max(0, int(os.getenv("ENRICHMENT_TAVILY_MAX_RETRIES", "1")))


    # FIRECRAWL_API_KEY is the Firecrawl Cloud bearer token used exclusively
    # for GST/turnover *search* (POST https://api.firecrawl.dev/v2/search -
    # see services/gst_turnover_enrichment/firecrawl_cloud_search.py). It is
    # never sent to the self-hosted instance below, which does not require a
    # key at all - only FIRECRAWL_API_URL needs to point at it for *scraping*
    # (services/gst_turnover_enrichment/firecrawl_client.py's .scrape()).
    FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
    FIRECRAWL_API_URL = os.getenv("FIRECRAWL_API_URL", "http://217.217.249.121:3002")
    FIRECRAWL_MAX_CONCURRENCY = max(1, int(os.getenv("FIRECRAWL_MAX_CONCURRENCY", "2")))
    FIRECRAWL_MAX_REQUESTS_PER_MINUTE = max(1, int(os.getenv("FIRECRAWL_MAX_REQUESTS_PER_MINUTE", "60")))
    FIRECRAWL_MAX_RETRIES = max(0, int(os.getenv("FIRECRAWL_MAX_RETRIES", "1")))
    FIRECRAWL_TIMEOUT_SECONDS = max(1, int(os.getenv("FIRECRAWL_TIMEOUT_SECONDS", "30")))
    FIRECRAWL_TIMEOUT_MS = max(
        1_000,
        int(os.getenv("FIRECRAWL_TIMEOUT_MS", str(FIRECRAWL_TIMEOUT_SECONDS * 1000))),
    )
    FIRECRAWL_MAX_PAGES = max(1, int(os.getenv("FIRECRAWL_MAX_PAGES", "5")))
    FIRECRAWL_MAX_PDFS_PER_PAGE = max(0, int(os.getenv("FIRECRAWL_MAX_PDFS_PER_PAGE", "2")))
    # GST/turnover company-name fallback.  These limits are intentionally
    # separate from the broader discovery/enrichment pipeline.
    MAX_SEARCH_QUERIES_PER_FIELD = max(1, int(os.getenv("MAX_SEARCH_QUERIES_PER_FIELD", "4")))
    MAX_RESULT_URLS_PER_FIELD = max(1, int(os.getenv("MAX_RESULT_URLS_PER_FIELD", "3")))
    MAX_FIRECRAWL_PAGES_PER_COMPANY = max(1, int(os.getenv("MAX_FIRECRAWL_PAGES_PER_COMPANY", "5")))
    FIRECRAWL_SEARCH_TIMEOUT_SECONDS = max(1, int(os.getenv("FIRECRAWL_SEARCH_TIMEOUT_SECONDS", "30")))
    FIRECRAWL_SEARCH_MAX_RETRIES = max(0, int(os.getenv("FIRECRAWL_SEARCH_MAX_RETRIES", "1")))


settings = Settings()
