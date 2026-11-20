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

## GoodBooks CRM import

The GoodBooks CRM page connects each user through
`ERP_LOGIN_URL` plus `ERP_AUTH_PATH`. The backend keeps the returned login
details in memory for 30 minutes and sends them in the `Login` header for
uploads. A backend restart requires users to connect again. Configure
`ERP_LOGIN_URL` and `ERP_AUTH_PATH` in `.env` when using another GoodBooks
server.

The current-search CRM action builds `LeadImport_template.xlsx` from the
selected rows. The manual CRM action sends a selected `.xlsx` file as-is.
Both actions use the authenticated user's `BaseUri` plus `/ads/Bulk/Import`.
If GoodBooks returns an internal localhost `BaseUri`, the backend uses
`ERP_LOGIN_URL` as the reachable API host. Configure `CRM_IMPORT_PATH`,
`CRM_IMPORT_FILE_TYPE`, `CRM_BULK_ICE_ID`, and
`CRM_REQUEST_TIMEOUT_SECONDS` in `.env` when their defaults differ.
