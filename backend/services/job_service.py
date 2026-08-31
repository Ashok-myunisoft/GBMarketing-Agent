"""Runs existing lead-generation workflows in background threads for the UI."""

import logging
import threading
from typing import Any

from agents.conversation_agent import ConversationAgent
from services.job_store import JobStore

logger = logging.getLogger(__name__)


class JobService:
    def __init__(self, store: JobStore | None = None):
        self.store = store or JobStore()

    def create(self, query: str) -> dict[str, Any]:
        job = self.store.create(query)
        thread = threading.Thread(target=self._run, args=(job["id"], query), daemon=True)
        thread.start()
        return job

    def _run(self, job_id: str, query: str) -> None:
        self.store.start(job_id)
        try:
            agent = ConversationAgent(
                progress_callback=lambda step, status, message: self.store.progress(job_id, step, status, message)
            )
            result = agent.execute(query)
            self.store.complete(job_id, result.model_dump(mode="json"))
        except Exception as exc:
            # Previously this exception (including export failures) was
            # only ever recorded as a bare str(exc) in the job store - the
            # server console/log showed nothing at all, so a failure like a
            # locked/open export file was effectively silent outside the
            # API. logger.exception prints the full traceback to the
            # server's own logs without changing what's stored for the UI.
            logger.exception("Job %s failed during workflow execution", job_id)
            self.store.fail(job_id, str(exc))
