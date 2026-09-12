"""End-to-end coverage for the exact bug just fixed: a company whose keys
match an EXISTING mlead row must have any newly-found field (GST,
turnover, ...) backfilled into that row, not silently discarded just
because the company itself isn't a "new" lead this run.

Exercises the real ValidationAgent.execute() (not the isolated helpers in
test_remember_in_mlead.py) so the interaction between the top-level dedup
check and the backfill/save decision is actually proven, not just each
piece in isolation.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from agents.validation_agent import ValidationAgent
from schemas.company import Company

_NO_SUCH_PATH = Path("no-such-existing-export.xlsx")

_EXISTING_MLEAD_ROW = {
    "lead_id": 7,
    "company_name": "Ambica Electro Control",
    "gst": None,  # this run finds it for the first time
    "website_url": "https://ambicaelectro.example",
    "mobile_number": None,
    "alternate_mobile_number": None,
    "email_id": None,
    "city": None,
    "industry_type": None,
    "region": None,
    "contact_person": None,
    "designation": None,
}


@patch("agents.validation_agent.MASTER_EXPORT_PATH", _NO_SUCH_PATH)
class MleadDedupBackfillTests(unittest.TestCase):
    def _run(self, companies):
        with patch("agents.validation_agent._mlead_repository") as mock_repo:
            mock_repo.fetch_all.return_value = [_EXISTING_MLEAD_ROW]
            kept = ValidationAgent().execute(companies, existing_excel_path=str(_NO_SUCH_PATH))
        return kept, mock_repo

    def test_company_matching_an_existing_mlead_row_is_backfilled_not_dropped_silently(self):
        # Same website as the existing row - this is the real-world "29
        # already in export" scenario: matches the baseline, so it's not a
        # new lead, but this run's enrichment found its GST for the first
        # time and that must not be thrown away.
        company = Company(
            company_name="Ambica Electro Control", website="https://ambicaelectro.example",
            gst="27AAPFU0939F1ZV", turnover="50 Crore",
        )
        kept, mock_repo = self._run([company])

        self.assertEqual(kept, [])  # correctly not a "new" lead
        mock_repo.save.assert_not_called()  # never re-inserted as a duplicate row
        mock_repo.backfill_blank_fields.assert_called_once()
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.args[0], 7)
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.kwargs["gst"], "27AAPFU0939F1ZV")
        self.assertEqual(mock_repo.backfill_blank_fields.call_args.kwargs["turnover"], 50.0)

    def test_genuinely_new_company_is_saved_not_backfilled(self):
        company = Company(
            company_name="Zenith Valves Pvt Ltd", website="https://zenithvalves.example",
            gst="33AAPFU0939F1Z2", turnover="20 Crore",
        )
        kept, mock_repo = self._run([company])

        self.assertEqual(len(kept), 1)
        mock_repo.backfill_blank_fields.assert_not_called()
        mock_repo.save.assert_called_once()
        self.assertEqual(mock_repo.save.call_args.kwargs["gst"], "33AAPFU0939F1Z2")

    def test_matching_company_with_nothing_new_still_does_not_crash(self):
        # Matches the baseline and has no new data either - backfill_blank_fields
        # is still called (it's a no-op internally when nothing is truthy),
        # never save().
        company = Company(company_name="Ambica Electro Control", website="https://ambicaelectro.example")
        kept, mock_repo = self._run([company])

        self.assertEqual(kept, [])
        mock_repo.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
