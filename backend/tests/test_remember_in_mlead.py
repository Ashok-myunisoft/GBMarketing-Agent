import unittest
from unittest.mock import MagicMock, patch

from agents.validation_agent import ValidationAgent
from schemas.company import Company


class RememberInMleadTests(unittest.TestCase):
    """_remember_in_mlead() is exercised directly (not through the full
    execute() pipeline, which needs a real Postgres connection for its own
    baseline load). It now always inserts - execute()'s dedup check handles
    the "already exists, may still have new data" case itself via
    _backfill_mlead(), before a company could ever reach here as a
    duplicate (see test_backfill_mlead_and_dedup_integration.py for that)."""

    def test_new_company_is_saved_with_turnover(self):
        # public."TLead"'s turn_over column is NUMERIC - _remember_in_mlead
        # must convert the text label ("50 Crore") to a plain Crore number
        # (see config/targeting.py's turnover_to_crore()) before it ever
        # reaches the repository, not pass the raw string through.
        company = Company(company_name="Ambica Electro Control", gst="27AAPFU0939F1ZV", turnover="50 Crore")
        keys = {"gst:27AAPFU0939F1ZV"}

        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.save.return_value = 99
            ValidationAgent()._remember_in_mlead(company, keys, postgres_key_to_id={})

        mock_repo.save.assert_called_once()
        mock_repo.backfill_blank_fields.assert_not_called()
        self.assertEqual(mock_repo.save.call_args.kwargs["turnover"], 50.0)
        self.assertEqual(mock_repo.save.call_args.kwargs["gst"], "27AAPFU0939F1ZV")

    def test_new_company_updates_the_in_memory_key_map(self):
        # So a second company later in the same run that matches this one
        # is caught by execute()'s own dedup check on the next iteration.
        company = Company(company_name="Ambica Electro Control", gst="27AAPFU0939F1ZV")
        postgres_key_to_id = {}
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.save.return_value = 55
            ValidationAgent()._remember_in_mlead(
                company, {"gst:27AAPFU0939F1ZV"}, postgres_key_to_id,
            )

        self.assertEqual(postgres_key_to_id["gst:27AAPFU0939F1ZV"], 55)

    def test_unconvertible_turnover_text_is_passed_as_none_not_dropped_silently(self):
        # A turnover value with no recognizable unit (turnover_to_crore()
        # returns None) must still let the rest of the row (GST, etc.)
        # get saved - it must not be treated as a reason to skip saving.
        company = Company(company_name="Ambica Electro Control", gst="27AAPFU0939F1ZV", turnover="undisclosed")
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.save.return_value = 1
            ValidationAgent()._remember_in_mlead(company, {"gst:27AAPFU0939F1ZV"}, {})

        mock_repo.save.assert_called_once()
        self.assertIsNone(mock_repo.save.call_args.kwargs["turnover"])
        self.assertEqual(mock_repo.save.call_args.kwargs["gst"], "27AAPFU0939F1ZV")

    def test_a_persistence_failure_never_raises(self):
        company = Company(company_name="Ambica Electro Control")
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.save.side_effect = RuntimeError("connection refused")
            ValidationAgent()._remember_in_mlead(company, {"name:ambica"}, {})
        # No exception propagated - this is the whole point of the try/except.


class BackfillMleadTests(unittest.TestCase):
    """_backfill_mlead() - called from execute()'s dedup check for a
    company that already exists in mlead but may have new data worth
    saving (the "GST/turnover found for a company that was already a
    duplicate" scenario)."""

    def test_backfills_the_existing_row_with_freshly_found_fields(self):
        company = Company(company_name="Ambica Electro Control", gst="27AAPFU0939F1ZV", turnover="50 Crore")
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            ValidationAgent()._backfill_mlead(7, company)

        mock_repo.save.assert_not_called()
        mock_repo.backfill_blank_fields.assert_called_once()
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.args[0], 7)
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.kwargs["gst"], "27AAPFU0939F1ZV")
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.kwargs["turnover"], 50.0)

    def test_a_backfill_failure_never_raises(self):
        company = Company(company_name="Ambica Electro Control")
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.backfill_blank_fields.side_effect = RuntimeError("connection refused")
            ValidationAgent()._backfill_mlead(7, company)
        # No exception propagated - this is the whole point of the try/except.


if __name__ == "__main__":
    unittest.main()
