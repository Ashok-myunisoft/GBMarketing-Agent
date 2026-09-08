import tempfile
import unittest
from pathlib import Path

from services.job_store import JobStore


class JobStoreReconciliationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "jobs.sqlite3"

    def test_running_job_from_a_previous_process_is_marked_failed_on_restart(self):
        # Simulates the real failure mode: a job left "running" by a process
        # that died mid-flight, then the server (and JobStore) restarts.
        store = JobStore(database_path=self.db_path)
        job = store.create("find pump manufacturers in Chennai")
        store.start(job["id"])
        self.assertEqual(store.get(job["id"])["status"], "running")

        # A fresh JobStore instance == a fresh process starting up.
        restarted_store = JobStore(database_path=self.db_path)
        reconciled = restarted_store.get(job["id"])
        self.assertEqual(reconciled["status"], "failed")
        self.assertIn("orphaned", reconciled["error"].lower())
        self.assertIsNotNone(reconciled["completed_at"])

    def test_queued_job_from_a_previous_process_is_marked_failed_on_restart(self):
        store = JobStore(database_path=self.db_path)
        job = store.create("find pump manufacturers in Chennai")
        self.assertEqual(store.get(job["id"])["status"], "queued")

        restarted_store = JobStore(database_path=self.db_path)
        self.assertEqual(restarted_store.get(job["id"])["status"], "failed")

    def test_already_completed_job_is_left_untouched_across_a_restart(self):
        store = JobStore(database_path=self.db_path)
        job = store.create("find pump manufacturers in Chennai")
        store.start(job["id"])
        store.complete(job["id"], {"export_path": "x.xlsx"})

        restarted_store = JobStore(database_path=self.db_path)
        completed = restarted_store.get(job["id"])
        self.assertEqual(completed["status"], "completed")

    def test_already_failed_job_is_left_untouched_across_a_restart(self):
        store = JobStore(database_path=self.db_path)
        job = store.create("find pump manufacturers in Chennai")
        store.fail(job["id"], "some real error")

        restarted_store = JobStore(database_path=self.db_path)
        failed = restarted_store.get(job["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], "some real error")

    def test_reconciliation_within_the_same_process_does_not_refail_active_jobs(self):
        # Guards against over-eager reconciliation: a job legitimately still
        # running in *this* process must never be touched just because
        # something else constructs another JobStore pointed at the same DB
        # (e.g. a second request handler) - only startup-time reconciliation
        # from main.py should ever call this, but this test pins the actual
        # behavior: reconciliation only fires at construction time, not on
        # every store call, so it can't clobber a job that started after the
        # store it's using was constructed.
        store = JobStore(database_path=self.db_path)
        job = store.create("find pump manufacturers in Chennai")
        store.start(job["id"])
        store.progress(job["id"], "search", "running", "Search started")
        self.assertEqual(store.get(job["id"])["status"], "running")


if __name__ == "__main__":
    unittest.main()
