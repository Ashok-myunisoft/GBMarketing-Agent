import unittest
from tempfile import TemporaryDirectory
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

    def test_company_in_requested_locality_is_kept(self):
        company = _company(
            city="Chennai", state="Tamil Nadu", district="Chennai", locality="Ambattur",
        )
        kept = self._run([company], requested_location="Ambattur")
        self.assertEqual(len(kept), 1)

    def test_company_only_confirmed_at_city_level_is_not_rejected_for_locality_request(self):
        company = _company(city="Chennai", state="Tamil Nadu")
        kept = self._run([company], requested_location="Ambattur")
        self.assertEqual(len(kept), 1)
        self.assertTrue(any("location" in note for note in kept[0].validation_notes))

    def test_duplicate_within_the_same_run_is_not_removed_by_validation(self):
        first = _company()
        second = _company()
        kept = self._run([first, second])
        self.assertEqual(len(kept), 2)

    def test_duplicate_diagnostic_reports_exact_key_source_and_record_id(self):
        existing_row = {
            "lead_id": 42, "company_name": "Focet Valves Private Limited",
            "gst": "33ABCDE1234F1Z5", "website_url": "https://focetvalves.example",
            "mobile_number": None, "alternate_mobile_number": None,
            "email_id": None, "city": None, "industry_type": None,
            "region": None, "contact_person": None, "designation": None,
        }
        candidate = _company(
            source_table="public.discovered_companies", source_row_id="company_id=91",
        )
        with patch("agents.validation_agent._mlead_repository") as repository:
            repository.fetch_all.return_value = [existing_row]
            agent = ValidationAgent()
            kept = agent.execute([candidate], existing_excel_path=str(_NO_SUCH_PATH))

        self.assertEqual(kept, [])
        removed = agent.removed_companies[0]
        self.assertEqual(removed["source_row_id"], "company_id=91")
        self.assertIn("name:focetvalvesprivatelimited", [m["identity_key"] for m in removed["matched_baselines"]])
        self.assertTrue(all(m["source"] == 'PostgreSQL public."TLead"' for m in removed["matched_baselines"]))
        self.assertTrue(all(m["record_id"] == "42" for m in removed["matched_baselines"]))
        self.assertIn("matched PostgreSQL public.\"TLead\" (42)", removed["reason"])

    def test_similar_name_and_different_identifiers_do_not_false_match_baseline(self):
        existing_row = {
            "lead_id": 43, "company_name": "Apex Valve Works",
            "gst": "33AAA1111111", "website_url": "https://apex-valve.example",
            "mobile_number": None, "alternate_mobile_number": None,
            "email_id": None, "city": None, "industry_type": None,
            "region": None, "contact_person": None, "designation": None,
        }
        candidate = _company(
            company_name="Apex Valve Works India", gst="33BBB2222222",
            website="https://apex-india.example",
        )
        with patch("agents.validation_agent._mlead_repository") as repository:
            repository.fetch_all.return_value = [existing_row]
            repository.save.return_value = 99
            agent = ValidationAgent()
            kept = agent.execute([candidate], existing_excel_path=str(_NO_SUCH_PATH))

        self.assertEqual(len(kept), 1)
        self.assertEqual(agent.last_run_stats["duplicates"], 0)
        self.assertEqual(agent.removed_companies, [])

    def test_exact_name_does_not_override_conflicting_gst_and_website(self):
        existing_row = {
            "lead_id": 44, "company_name": "Apex Valve Works",
            "gst": "33AAA1111111", "website_url": "https://apex-one.example",
            "mobile_number": None, "alternate_mobile_number": None,
            "email_id": None, "city": None, "industry_type": None,
            "region": None, "contact_person": None, "designation": None,
        }
        candidate = _company(
            company_name="Apex Valve Works", gst="33BBB2222222",
            website="https://apex-two.example",
        )
        with patch("agents.validation_agent._mlead_repository") as repository:
            repository.fetch_all.return_value = [existing_row]
            repository.save.return_value = 100
            agent = ValidationAgent()
            kept = agent.execute([candidate], existing_excel_path=str(_NO_SUCH_PATH))

        self.assertEqual(len(kept), 1)
        self.assertEqual(agent.last_run_stats["duplicates"], 0)

    def test_shared_phone_does_not_override_conflicting_gst(self):
        existing_row = {
            "lead_id": 45, "company_name": "Riverside Components",
            "gst": "33AAA1111111", "website_url": "https://riverside-one.example",
            "mobile_number": "9876543210", "alternate_mobile_number": None,
            "email_id": None, "city": None, "industry_type": None,
            "region": None, "contact_person": None, "designation": None,
        }
        candidate = _company(
            company_name="Riverside Industrial Parts", gst="33BBB2222222",
            website="https://riverside-two.example", phone="9876543210",
        )
        with patch("agents.validation_agent._mlead_repository") as repository:
            repository.fetch_all.return_value = [existing_row]
            repository.save.return_value = 101
            agent = ValidationAgent()
            kept = agent.execute([candidate], existing_excel_path=str(_NO_SUCH_PATH))

        self.assertEqual(len(kept), 1)
        self.assertEqual(agent.last_run_stats["duplicates"], 0)

    def test_shared_phone_does_not_match_different_names_when_gst_is_missing(self):
        existing_row = {
            "lead_id": 46, "company_name": "East Valley Tools",
            "gst": None, "website_url": "https://east-valley.example",
            "mobile_number": "9876543210", "alternate_mobile_number": None,
            "email_id": None, "city": None, "industry_type": None,
            "region": None, "contact_person": None, "designation": None,
        }
        candidate = _company(
            company_name="West Valley Pumps", gst=None,
            website="https://west-valley.example", phone="9876543210",
        )
        with patch("agents.validation_agent._mlead_repository") as repository:
            repository.fetch_all.return_value = [existing_row]
            repository.save.return_value = 102
            agent = ValidationAgent()
            kept = agent.execute([candidate], existing_excel_path=str(_NO_SUCH_PATH))

        self.assertEqual(len(kept), 1)
        self.assertEqual(agent.last_run_stats["duplicates"], 0)

    def test_file_baseline_diagnostic_reports_filename_and_row_number(self):
        with TemporaryDirectory() as directory:
            baseline = Path(directory) / "baseline.csv"
            baseline.write_text(
                "company_name,website\nFocet Valves Private Limited,focetvalves.example\n",
                encoding="utf-8",
            )
            with patch("agents.validation_agent._mlead_repository") as repository:
                repository.fetch_all.return_value = []
                agent = ValidationAgent()
                kept = agent.execute([_company()], existing_excel_path=str(baseline))

        self.assertEqual(kept, [])
        matches = agent.removed_companies[0]["matched_baselines"]
        self.assertTrue(any(match["source"] == "file baseline.csv" and match["record_id"] == "row 2" for match in matches))


if __name__ == "__main__":
    unittest.main()
