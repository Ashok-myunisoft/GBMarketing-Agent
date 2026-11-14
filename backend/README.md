# Backend configuration

## Company discovery database

Lead searches read matching rows from `public.discovered_companies`. Set
`DISCOVERED_COMPANIES_DATABASE_URL` in the project-root `.env` file to the
database connection URL. This setting is separate from `DATABASE_URL`, which
continues to serve the existing application persistence paths. The discovery
repository supports the SQLAlchemy URL prefix `postgresql+psycopg2://` and
uses read-only database connections.

## Diagnosing slow searches

The backend writes `[PERF]` timing messages to its standard log output. They
include total job time, search and workflow-step durations, enrichment batch
durations, per-company enrichment stage timings, and Gemini research request
durations. Search and validation batch progress events also include elapsed
time in the job event history.

For Docker Compose, follow the backend log and filter for timing records:

```powershell
docker compose logs --follow backend | Select-String '\[PERF\]'
```

For a local backend started with `python backend/main.py`, read the same
`[PERF]` messages from that process's console output.
