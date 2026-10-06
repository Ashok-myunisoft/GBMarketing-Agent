# Backend configuration

## Company discovery database

Lead searches read matching rows from `public.discovered_companies`. Set
`DISCOVERED_COMPANIES_DATABASE_URL` in the project-root `.env` file to the
database connection URL. This setting is separate from `DATABASE_URL`, which
continues to serve the existing application persistence paths. The discovery
repository supports the SQLAlchemy URL prefix `postgresql+psycopg2://` and
uses read-only database connections.
