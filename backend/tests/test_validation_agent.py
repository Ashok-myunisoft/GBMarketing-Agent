import unittest
from pathlib import Path
from unittest.mock import patch

from agents.validation_agent import ValidationAgent
from schemas.company import Company


_NO_SUCH_PATH = Path("no-such-existing-export.xlsx")


def _company(**overrides) -> Company:
    defaults = dict(company_name="Focet Valves Private Limited", website="https://focetvalves.example")
    defaults.update(overrides)
    return Company(**defaults)


@patch("agents.validation_agent.MASTER_EXPORT_PATH", _NO_SUCH_PATH)
class ValidationAgentTests(unittest.TestCase):
    def _run(self, companies, **kwargs):
        return ValidationAgent().execute(
            companies, existing_excel_path=str(_NO_SUCH_PATH), **kwargs
        )

    def test_missing_industry_is_kept_as_unverified_not_rejected(self):
        company = _company(industry=None)
        kept = self._run([company], requested_industry="Valve")
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].validation_status, "unverified")

    def test_confirmed_different_industry_is_rejected(self):
        company = _company(industry="Pump manufacturer")
        kept = self._run([company], requested_industry="Valve")
        self.assertEqual(kept, [])

    def test_matching_industry_is_kept(self):
        company = _company(industry="Valve manufacturer")
        kept = self._run([company], requested_industry="Valve")
        self.assertEqual(len(kept), 1)

    def test_low_turnover_without_explicit_filter_is_kept(self):
        company = _company(turnover="2 Cr")
        kept = self._run([company])
        self.assertEqual(len(kept), 1)
        self.assertFalse(any(note.startswith("rejected:") for note in kept[0].validation_notes))

    def test_low_turnover_with_explicit_filter_is_rejected(self):
        company = _company(turnover="2 Cr")
        kept = self._run([company], requested_turnover_floor_cr=50.0)
        self.assertEqual(kept, [])

    def test_turnover_above_explicit_floor_is_kept(self):
        company = _company(turnover="80 Cr")
        kept = self._run([company], requested_turnover_floor_cr=50.0)
        self.assertEqual(len(kept), 1)

    def test_low_employee_count_without_explicit_filter_is_kept(self):
        company = _company(employee_count="10")
        kept = self._run([company])
        self.assertEqual(len(kept), 1)

    def test_low_employee_count_with_explicit_filter_is_rejected(self):
        company = _company(employee_count="10")
        kept = self._run([company], requested_employee_floor=100.0)
        self.assertEqual(kept, [])

    def test_missing_gst_without_explicit_requirement_is_kept(self):
        company = _company(gst=None)
        kept = self._run([company])
        self.assertEqual(len(kept), 1)

    def test_missing_gst_with_explicit_requirement_is_rejected(self):
        company = _company(gst=None)
        kept = self._run([company], requested_gst_required=True)
        self.assertEqual(kept, [])

    def test_company_outside_requested_state_is_dropped(self):
        company = _company(city="Pune", state="Maharashtra", address="Pune, Maharashtra, India")
        kept = self._run([company], requested_location="Gujarat")
        self.assertEqual(kept, [])

    def test_company_inside_requested_state_is_kept_regardless_of_city(self):
        for city in ("Ahmedabad", "Gandhinagar", "Vadodara"):
            company = _company(
                company_name=f"Company in {city}",
                website=f"https://{city.lower()}.example",
                city=city, state="Gujarat", address=f"{city}, Gujarat, India",
            )
            kept = self._run([company], requested_location="Gujarat")
            self.assertEqual(len(kept), 1, city)

    def test_company_with_unresolvable_location_is_kept_with_a_note(self):
        company = _company(city=None, state=None, address=None)
        kept = self._run([company], requested_location="Gujarat")
        self.assertEqual(len(kept), 1)
        self.assertTrue(any("location" in note for note in kept[0].validation_notes))

    def test_duplicate_within_the_same_run_is_dropped(self):
        first = _company()
        second = _company()
        kept = self._run([first, second])
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
