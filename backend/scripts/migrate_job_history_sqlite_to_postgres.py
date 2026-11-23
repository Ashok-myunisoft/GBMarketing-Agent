"""One-time copy of a chosen jobs.sqlite3 history file into CMS PostgreSQL.

Run from backend/: python scripts/migrate_job_history_sqlite_to_postgres.py PATH
The target is DATABASE_URL from the project .env. The script refuses to merge
into a non-empty Marketing Agent PostgreSQL job table.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import psycopg2

from core.config import settings
from services.job_store import JobStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_file", type=Path, help="SQLite jobs database to import")
    args = parser.parse_args()

    source = args.sqlite_file.expanduser().resolve()
    if not source.is_file():
        parser.error(f"SQLite file not found: {source}")
    if not settings.DATABASE_URL:
        parser.error("DATABASE_URL is missing from the project .env")

    # Create the destination tables using the same schema as the application.
    JobStore(database_url=settings.DATABASE_URL)

    with sqlite3.connect(source) as sqlite_db:
        sqlite_db.row_factory = sqlite3.Row
        jobs = sqlite_db.execute("SELECT * FROM jobs ORDER BY created_at, id").fetchall()
        events = sqlite_db.execute("SELECT * FROM job_events ORDER BY id").fetchall()
        uploads = sqlite_db.execute("SELECT * FROM crm_uploads ORDER BY created_at, id").fetchall()

    pg = psycopg2.connect(settings.DATABASE_URL)
    try:
        with pg:
            with pg.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM marketing_agent_jobs")
                if cursor.fetchone()[0]:
                    raise RuntimeError(
                        "PostgreSQL already contains Marketing Agent jobs. "
                        "Import stopped to avoid mixing histories; ask an administrator to review first."
                    )
                for row in jobs:
                    cursor.execute(
                        """INSERT INTO marketing_agent_jobs
                        (id, query, status, current_step, created_at, started_at, completed_at,
                         error, result_json, export_path, incharge, alloted_to)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        tuple(dict(row).get(key, "") if key in ("incharge", "alloted_to") else row[key] for key in (
                            "id", "query", "status", "current_step", "created_at", "started_at",
                            "completed_at", "error", "result_json", "export_path", "incharge", "alloted_to",
                        )),
                    )
                for row in events:
                    cursor.execute(
                        """INSERT INTO marketing_agent_job_events
                        (id, job_id, created_at, step, status, message)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                        tuple(row[key] for key in ("id", "job_id", "created_at", "step", "status", "message")),
                    )
                for row in uploads:
                    cursor.execute(
                        """INSERT INTO marketing_agent_crm_uploads
                        (id, job_id, source_type, file_name, status, item_count, response_json,
                         error, created_at, completed_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        tuple(row[key] for key in (
                            "id", "job_id", "source_type", "file_name", "status", "item_count",
                            "response_json", "error", "created_at", "completed_at",
                        )),
                    )
                cursor.execute(
                    """SELECT setval(pg_get_serial_sequence('marketing_agent_job_events', 'id'),
                       GREATEST(COALESCE((SELECT MAX(id) FROM marketing_agent_job_events), 1), 1),
                       EXISTS(SELECT 1 FROM marketing_agent_job_events))"""
                )
    finally:
        pg.close()

    print(f"Imported {len(jobs)} jobs, {len(events)} events, and {len(uploads)} CRM upload records.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
