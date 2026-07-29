"""Runs existing lead-generation workflows in background threads for the UI."""

import threading
from typing import Any

from agents.conversation_agent import ConversationAgent
from services.job_store import JobStore


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
            self.store.fail(job_id, str(exc))
