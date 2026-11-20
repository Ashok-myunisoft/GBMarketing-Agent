"""Runs existing lead-generation workflows in background threads for the UI."""

import logging
import threading
import time
from typing import Any

from agents.conversation_agent import ConversationAgent
from services.job_store import JobStore

logger = logging.getLogger(__name__)


class JobService:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()

    def create(self, query: str, incharge: str = "", alloted_to: str = "") -> dict[str, Any]:
        job = self.store.create(query, incharge=incharge, alloted_to=alloted_to)
        thread = threading.Thread(target=self._run, args=(job["id"], query), daemon=True)
        thread.start()
        return job

    def _run(self, job_id: str, query: str) -> None:
        job_started = time.monotonic()
        logger.info("[PERF] job started job_id=%s query=%r", job_id, query)
        self.store.start(job_id)
        try:
            agent = ConversationAgent(
                progress_callback=lambda step, status, message: self.store.progress(job_id, step, status, message)
            )
            result = agent.execute(query)
            self.store.complete(job_id, result.model_dump(mode="json"))
            logger.info(
                "[PERF] job completed job_id=%s elapsed_ms=%d leads=%d",
                job_id,
                round((time.monotonic() - job_started) * 1000),
                len(result.companies),
            )
        except Exception as exc:
            # Previously this exception (including export failures) was
            # only ever recorded as a bare str(exc) in the job store - the
            # server console/log showed nothing at all, so a failure like a
            # locked/open export file was effectively silent outside the
            # API. logger.exception prints the full traceback to the
            # server's own logs without changing what's stored for the UI.
            logger.exception("Job %s failed during workflow execution", job_id)
            self.store.fail(job_id, str(exc))
            logger.info(
                "[PERF] job failed job_id=%s elapsed_ms=%d",
                job_id,
                round((time.monotonic() - job_started) * 1000),
            )
