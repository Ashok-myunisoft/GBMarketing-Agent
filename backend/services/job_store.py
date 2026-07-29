"""SQLite-backed storage for frontend lead-generation jobs."""

import json
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


BACKEND_DIR = Path(__file__).resolve().parent.parent


class JobStore:
    def __init__(self, database_path: Path | str = BACKEND_DIR / "data" / "jobs.sqlite3"):
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
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
                    export_path TEXT
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
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, query: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        created_at = self._now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute(
                "INSERT INTO jobs (id, query, status, created_at) VALUES (?, ?, ?, ?)",
                (job_id, query, "queued", created_at),
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

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, created_at, step, status, message FROM job_events WHERE job_id = ? ORDER BY id",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
