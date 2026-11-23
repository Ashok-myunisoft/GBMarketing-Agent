"""Persistent storage for frontend lead-generation jobs.

PostgreSQL is used when DATABASE_URL is configured. SQLite remains the local
fallback and can also be selected explicitly by passing database_path.
"""

import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from psycopg2 import connect as pg_connect
from psycopg2.extras import RealDictCursor

from core.config import settings


BACKEND_DIR = Path(__file__).resolve().parent.parent


class _PostgresConnection:
    """Small compatibility layer so the existing store methods work on both DBs."""

    _tables = {"jobs": "marketing_agent_jobs", "job_events": "marketing_agent_job_events", "crm_uploads": "marketing_agent_crm_uploads"}

    def __init__(self, dsn: str):
        self._connection = pg_connect(dsn)
        self._connection.autocommit = True

    def execute(self, sql: str, params: tuple = ()):
        for old, new in self._tables.items():
            sql = re.sub(rf"\b{old}\b", new, sql)
        sql = sql.replace("?", "%s")
        cursor = self._connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql, params)
        return cursor

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)

    def table_columns(self, table: str) -> set[str]:
        cursor = self.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (self._tables[table],),
        )
        return {row["column_name"] for row in cursor.fetchall()}

    def close(self) -> None:
        self._connection.close()


class JobStore:
    def __init__(
        self,
        database_path: Path | str | None = None,
        *,
        database_url: str | None = None,
    ):
        # Explicit paths preserve SQLite behavior for tests and local tools.
        # The deployed app uses the existing CMS PostgreSQL URL by default.
        self._database_url = database_url if database_url is not None else (
            None if database_path is not None else settings.DATABASE_URL
        )
        self._path = Path(database_path or BACKEND_DIR / "data" / "jobs.sqlite3")
        if not self._database_url:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()
        self._reconcile_orphaned_jobs()

    def _connect(self):
        if self._database_url:
            return _PostgresConnection(self._database_url)
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            if self._database_url:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS marketing_agent_jobs (
                        id TEXT PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL,
                        current_step TEXT, created_at TEXT NOT NULL, started_at TEXT,
                        completed_at TEXT, error TEXT, result_json TEXT, export_path TEXT,
                        incharge TEXT NOT NULL DEFAULT '', alloted_to TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE IF NOT EXISTS marketing_agent_job_events (
                        id BIGSERIAL PRIMARY KEY, job_id TEXT NOT NULL REFERENCES marketing_agent_jobs(id),
                        created_at TEXT NOT NULL, step TEXT, status TEXT NOT NULL, message TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS marketing_agent_crm_uploads (
                        id TEXT PRIMARY KEY, job_id TEXT, source_type TEXT NOT NULL,
                        file_name TEXT NOT NULL, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0,
                        response_json TEXT, error TEXT, created_at TEXT NOT NULL, completed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS marketing_agent_jobs_created_at_idx
                        ON marketing_agent_jobs(created_at DESC);
                    CREATE INDEX IF NOT EXISTS marketing_agent_events_job_id_idx
                        ON marketing_agent_job_events(job_id, id);
                    CREATE INDEX IF NOT EXISTS marketing_agent_uploads_created_at_idx
                        ON marketing_agent_crm_uploads(created_at DESC);
                    """
                )
                columns = connection.table_columns("jobs")
                for name in ("incharge", "alloted_to"):
                    if name not in columns:
                        connection.execute(
                            f"ALTER TABLE jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                        )
                return
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error TEXT,
                    result_json TEXT,
                    export_path TEXT,
                    incharge TEXT NOT NULL DEFAULT '',
                    alloted_to TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    step TEXT,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
                CREATE TABLE IF NOT EXISTS crm_uploads (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    source_type TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    response_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
            for name in ("incharge", "alloted_to"):
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _reconcile_orphaned_jobs(self) -> None:
        """Fails any job left "queued"/"running" from a previous process.

        Each job runs as a plain daemon thread (see JobService._run) with no
        persistence of its in-memory state: if the server restarts (deploy,
        crash, dev-server reload) while a job is mid-flight, that thread is
        simply gone, but its row was never updated - it sits at "running"
        forever with no error, indistinguishable in the UI from genuinely
        still working. A brand-new JobStore in a brand-new process cannot
        have a live thread for any job already marked "queued"/"running" in
        the database, so every one of them is, by definition, orphaned from
        an earlier process. Runs once per process (JobService constructs a
        single JobStore instance at import time), not on every request.
        """
        with self._lock, closing(self._connect()) as connection:
            orphaned = connection.execute(
                "SELECT id FROM jobs WHERE status IN ('queued', 'running')"
            ).fetchall()
            if not orphaned:
                return
            now = self._now()
            error = "Job did not complete before the server restarted (orphaned from a previous process)"
            for row in orphaned:
                connection.execute(
                    "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                    ("failed", now, error, row["id"]),
                )
                self._add_event(connection, row["id"], None, "failed", error)

    def create(self, query: str, incharge: str = "", alloted_to: str = "") -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created_at = self._now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO jobs (id, query, status, created_at, incharge, alloted_to) VALUES (?, ?, ?, ?, ?, ?)",
                (job_id, query, "queued", created_at, incharge, alloted_to),
            )
            self._add_event(connection, job_id, None, "queued", "Lead-generation job queued")
        return self.get(job_id) or {}

    def start(self, job_id: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, started_at = ? WHERE id = ?",
                ("running", self._now(), job_id),
            )
            self._add_event(connection, job_id, None, "running", "Workflow started")

    def progress(self, job_id: str, step: str, status: str, message: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE jobs SET current_step = ?, status = ? WHERE id = ?",
                (step, "running", job_id),
            )
            self._add_event(connection, job_id, step, status, message)

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        export_path = result.get("export_path")
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """UPDATE jobs
                   SET status = ?, current_step = ?, completed_at = ?, result_json = ?, export_path = ?
                   WHERE id = ?""",
                ("completed", "export", self._now(), json.dumps(result), export_path, job_id),
            )
            self._add_event(connection, job_id, "export", "completed", "Workflow completed")

    def fail(self, job_id: str, error: str) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "UPDATE jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                ("failed", self._now(), error, job_id),
            )
            self._add_event(connection, job_id, None, "failed", error)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def job_counts(self) -> dict[str, int]:
        """Count all stored jobs, independently of the recent-jobs limit."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total_jobs, "
                "COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed_runs "
                "FROM jobs"
            ).fetchone()
        return {"total_jobs": row["total_jobs"], "completed_runs": row["completed_runs"]}

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, created_at, step, status, message FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def begin_crm_upload(
        self,
        upload_id: str,
        *,
        job_id: Optional[str],
        source_type: str,
        file_name: str,
        item_count: int,
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """INSERT INTO crm_uploads
                   (id, job_id, source_type, file_name, status, item_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    upload_id, job_id, source_type, file_name, "running",
                    item_count, self._now(),
                ),
            )

    def finish_crm_upload(
        self,
        upload_id: str,
        *,
        status: str,
        response: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                """UPDATE crm_uploads
                   SET status = ?, response_json = ?, error = ?, completed_at = ?
                   WHERE id = ?""",
                (
                    status,
                    json.dumps(response, ensure_ascii=False) if response is not None else None,
                    error,
                    self._now(),
                    upload_id,
                ),
            )

    def crm_upload_history(
        self, *, job_id: Optional[str] = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            if job_id:
                rows = connection.execute(
                    """SELECT * FROM crm_uploads WHERE job_id = ?
                       ORDER BY created_at DESC LIMIT ?""",
                    (job_id, max(1, min(limit, 200))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM crm_uploads ORDER BY created_at DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
        uploads = []
        for row in rows:
            upload = dict(row)
            upload["response"] = json.loads(upload.pop("response_json") or "null")
            uploads.append(upload)
        return uploads

    @staticmethod
    def _add_event(
        connection: sqlite3.Connection, job_id: str, step: Optional[str], status: str, message: str
    ) -> None:
        connection.execute(
            "INSERT INTO job_events (job_id, created_at, step, status, message) VALUES (?, ?, ?, ?, ?)",
            (job_id, JobStore._now(), step, status, message),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        result = json.loads(job.pop("result_json") or "null")
        job["result"] = result
        if result:
            job["lead_count"] = len(result.get("companies", []))
        else:
            job["lead_count"] = 0
        return job
