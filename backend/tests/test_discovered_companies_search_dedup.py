import unittest
from types import SimpleNamespace
from unittest.mock import patch

from schemas.company import Company
from services.discovered_companies_repository import DiscoveredCompaniesSearchService
from schemas.search_request import SearchRequest


class _DiscoveryCursor:
    def __init__(self):
        self.execute_count = 0
        self.rows = []
        self.description = []
        self.main_query_parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _query, parameters=None):
        self.execute_count += 1
        if self.execute_count == 1:
            self.description = [
                SimpleNamespace(name=name)
                for name in ("id", "company_name", "industry", "locations")
            ]
        elif self.execute_count == 2:
            self.rows = [{"column_name": "id"}]
        elif self.execute_count == 3:
            self.rows = [{"value": "Vedasandūr"}, {"value": "Dindigul"}]
        else:
            self.main_query_parameters = parameters
            self.rows = [{
                "id": 1,
                "company_name": "Sample Pump Works",
                "industry": "Pump",
                "locations": ["Vedasandūr", "Dindigul"],
            }]

    def fetchall(self):
        return self.rows


class _DiscoveryConnection:
    def __init__(self):
        self.fake_cursor = _DiscoveryCursor()

    def set_session(self, **_kwargs):
        pass

    def cursor(self, **_kwargs):
        return self.fake_cursor

    def close(self):
        pass


class StubRepository:
    def __init__(self, companies):
        self.companies = companies

    def search(self, _request):
        return self.companies


def _request():
    return SearchRequest(industry="Manufacturing", location="Krishnagiri", max_results=100)


def _verified(name, industry="Manufacturing", location="Krishnagiri", **kwargs):
    return Company(
        company_name=name, industry=industry, city=location,
        source_metadata={"source_data": {
            "company_industry_verified": True,
            "target_industry_match": "matched",
            "industry_evidence": {"provider": "google_maps", "provider_category": industry},
            "location_label_evidence": {"location:1": {"basis": "address", "labels": [location]}},
            "address_evidence": [{"address": f"Industrial Estate, {location}", "role": "provider_listed_business_location"}],
        }}, **kwargs,
    )


class DiscoveredCompaniesSearchDedupTests(unittest.TestCase):
    def test_generic_manufacturing_search_has_no_industry_db_filter(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        self.assertEqual(
            DiscoveredCompaniesRepository._industry_filter_terms("Manufacturing industries"),
            [],
        )

    def test_specific_pump_search_keeps_pump_industry_filter(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        self.assertEqual(
            DiscoveredCompaniesRepository._industry_filter_terms(
                "Pump manufacturing industries"
            ),
            ["Pump"],
        )

    def test_plain_ascii_location_resolves_to_accented_db_spelling(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        resolved = DiscoveredCompaniesRepository._resolve_location_term_groups(
            [["Vedasandur"], ["Dindigul"]],
            ["Vedasandūr", "Dindigul", "Virupakshi"],
        )

        self.assertEqual(resolved, [["Vedasandur", "Vedasandūr"], ["Dindigul"]])

    def test_generic_and_specific_searches_build_expected_db_filters(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        for industry, expected_industry_term in (
            ("Manufacturing industries", None),
            ("Pump manufacturing industries", "%Pump%"),
        ):
            with self.subTest(industry=industry):
                connection = _DiscoveryConnection()
                with patch(
                    "services.discovered_companies_repository.psycopg2.connect",
                    return_value=connection,
                ):
                    rows = DiscoveredCompaniesRepository(dsn="postgresql://test").search(
                        SearchRequest(
                            industry=industry,
                            location="Vedasandur",
                            max_results=10,
                        )
                    )

                self.assertEqual(len(rows), 1)
                parameters = connection.fake_cursor.main_query_parameters
                self.assertIn("%Vedasandūr%", parameters)
                if expected_industry_term:
                    self.assertIn(expected_industry_term, parameters)
                else:
                    self.assertFalse(any("Pump" in str(value) for value in parameters))

    def test_duplicate_records_with_same_gst_are_collapsed_with_provenance(self):
        first = _verified(
            "Kaveri Components Pvt Ltd", gst="33ABCDE1234F1Z5",
            source_table="public.discovered_companies", source_row_id="id=10",
        )
        duplicate = first.model_copy(update={"source_row_id": "id=11"})
        service = DiscoveredCompaniesSearchService(StubRepository([first, duplicate]))

        result = service.search(_request())

        self.assertEqual(result, [first])
        self.assertEqual(service.last_run_stats["raw_fetched"], 2)
        self.assertEqual(service.last_run_stats["after_dedup"], 1)
        self.assertEqual(service.last_run_stats["removed"][0]["matched_identity_key"], "gst:33ABCDE1234F1Z5")
        self.assertEqual(service.last_run_stats["removed"][0]["source_row_id"], "id=11")

    def test_distinct_companies_with_similar_names_are_not_merged(self):
        companies = [
            _verified("Apex Valve Works", industry="Manufacturing", gst="33AAA1111111", website="apex-valve.example"),
            _verified("Apex Valve Works India", industry="Manufacturing", gst="33BBB2222222", website="apex-india.example"),
        ]
        service = DiscoveredCompaniesSearchService(StubRepository(companies))

        self.assertEqual(len(service.search(_request())), 2)

    def test_name_similarity_and_short_shared_phone_do_not_merge(self):
        companies = [
            _verified("North Star Fabrication", phone="1234567"),
            _verified("North Star Fabrications", phone="1234567"),
        ]
        service = DiscoveredCompaniesSearchService(StubRepository(companies))

        self.assertEqual(len(service.search(_request())), 2)
        self.assertEqual(service.last_run_stats["removed"], [])

    def test_shared_website_with_conflicting_gst_does_not_merge(self):
        companies = [
            _verified("Apex Valve Works", industry="Manufacturing", gst="33AAA1111111", website="group.example"),
            _verified("Apex Valve Works India", industry="Manufacturing", gst="33BBB2222222", website="group.example"),
        ]
        service = DiscoveredCompaniesSearchService(StubRepository(companies))

        self.assertEqual(len(service.search(_request())), 2)

    def test_shared_contact_or_domain_does_not_merge_different_names_without_gst(self):
        companies = [
            _verified("East Valley Tools", website="group.example", email="info@group.example"),
            _verified("West Valley Pumps", website="group.example", email="info@group.example"),
        ]
        service = DiscoveredCompaniesSearchService(StubRepository(companies))

        self.assertEqual(len(service.search(_request())), 2)

    def test_repository_mapping_preserves_source_row_and_metadata(self):
        company = Company(company_name="Sample Components")
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        sourced = DiscoveredCompaniesRepository._attach_provenance(
            company,
            {"company_id": 71, "source_data": {"provider": "discovery"}, "source_url": "https://source.example"},
            ["company_id"],
            ["source_data", "source_url"],
        )
        self.assertEqual(sourced.source_table, "public.discovered_companies")
        self.assertEqual(sourced.source_row_id, "company_id=71")
        self.assertEqual(sourced.source_metadata["source_url"], "https://source.example")
        self.assertEqual(sourced.source_metadata["source_data"], {"provider": "discovery"})

    def test_generic_google_maps_label_is_removed_with_source_explanation(self):
        company = _verified(
            "Google Maps", source_table="public.discovered_companies",
            source_row_id="id=72",
        )
        repeated = company.model_copy(update={"source_row_id": "id=73"})
        service = DiscoveredCompaniesSearchService(StubRepository([company, repeated]))

        self.assertEqual(service.search(_request()), [])
        removed = service.last_run_stats["removed"][0]
        self.assertEqual(removed["stage"], "search_filter")
        self.assertEqual(removed["source_row_id"], "id=72")
        self.assertEqual(removed["occurrence_count"], 2)
        self.assertEqual(removed["source_row_ids"], ["id=72", "id=73"])
        self.assertIn("not a business name", removed["reason"])

    def test_genuine_company_name_containing_google_maps_is_preserved(self):
        company = _verified(
            "Google Maps Logistics Private Limited",
            gst="33AAA1111111", website="google-maps-logistics.example",
        )
        service = DiscoveredCompaniesSearchService(StubRepository([company]))

        self.assertEqual(service.search(_request()), [company])
        self.assertEqual(service.last_run_stats["removed"], [])

    def test_same_name_shared_phone_but_conflicting_websites_are_not_merged(self):
        companies = [
            _verified("Sri Lakshmi Industries", phone="9876543210", website="lakshmi-east.example"),
            _verified("Sri Lakshmi Industries", phone="9876543210", website="lakshmi-west.example"),
        ]
        service = DiscoveredCompaniesSearchService(StubRepository(companies))

        self.assertEqual(len(service.search(_request())), 2)

    def test_broad_manufacturing_accepts_verified_specific_manufacturing_sectors(self):
        pump = _verified("Aqua Pump Works", industry="Pump")
        equipment = _verified("Industrial Equipment Works", industry="Industrial equipments")
        result = DiscoveredCompaniesSearchService(StubRepository([pump, equipment])).search(_request())
        self.assertEqual([c.company_name for c in result], ["Aqua Pump Works", "Industrial Equipment Works"])

    def test_broad_manufacturing_keeps_all_location_industry_labels(self):
        print_shop = _verified("City Print House", industry="Paper, printing and packaging")
        print_shop.source_metadata["source_data"]["industry_evidence"]["provider_category"] = "Printing shop"
        maker = _verified("Kaveri Components", industry="Precision CNC Manufacturing")
        result = DiscoveredCompaniesSearchService(StubRepository([print_shop, maker])).search(_request())
        self.assertEqual(
            [c.company_name for c in result],
            ["City Print House", "Kaveri Components"],
        )

    def test_manufacturer_with_supplier_category_is_preserved(self):
        company = _verified("Aqua Pump Manufacturer", industry="Pump")
        company.source_metadata["source_data"]["industry_evidence"]["provider_category"] = "Pump manufacturer and supplier"
        self.assertEqual(DiscoveredCompaniesSearchService(StubRepository([company])).search(_request()), [company])

    def test_specific_pump_search_requires_verified_pump_industry(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository

        self.assertEqual(
            DiscoveredCompaniesRepository._industry_filter_terms(
                "Pump manufacturing industries"
            ),
            ["Pump"],
        )

    def test_unverified_and_query_derived_industries_are_removed(self):
        unverified = _verified("Unverified Pumps", industry="Pump")
        unverified.source_metadata["source_data"]["company_industry_verified"] = False
        query_only = _verified("Query Tagged Pumps", industry="Pump")
        query_only.source_metadata["source_data"].update({"company_industry_verified": False, "target_industry_match": "unverified"})
        request = SearchRequest(industry="Pump", location="Krishnagiri", max_results=10)
        service = DiscoveredCompaniesSearchService(StubRepository([unverified, query_only]))
        self.assertEqual(service.search(request), [])
        self.assertEqual(len(service.last_run_stats["removed"]), 2)

    def test_registered_office_only_location_is_not_operating_location(self):
        company = _verified("Registered Only Pump", industry="Pump")
        company.source_metadata["source_data"]["address_evidence"][0]["role"] = "registered_or_head_office"
        request = SearchRequest(industry="Pump", location="Krishnagiri", max_results=10)
        self.assertEqual(DiscoveredCompaniesSearchService(StubRepository([company])).search(request), [])

    def test_verified_operating_location_is_eligible(self):
        company = _verified("Operating Pump Works", industry="Pump")
        request = SearchRequest(industry="Pump", location="Krishnagiri", max_results=10)
        self.assertEqual(DiscoveredCompaniesSearchService(StubRepository([company])).search(request), [company])

    def test_duplicate_overfetch_respects_unique_result_limit(self):
        first = _verified("Unique Pump Works", industry="Pump", gst="33ABCDE1234F1Z5")
        duplicate = first.model_copy(update={"source_row_id": "id=2"})
        second = _verified("Second Pump Works", industry="Pump", gst="33ABCDF1234F1Z5")
        request = SearchRequest(industry="Pump", location="Krishnagiri", max_results=2)
        result = DiscoveredCompaniesSearchService(StubRepository([first, duplicate, second])).search(request)
        self.assertEqual([item.company_name for item in result], ["Unique Pump Works", "Second Pump Works"])

    def test_repository_uses_bounded_overfetch(self):
        from services.discovered_companies_repository import DiscoveredCompaniesRepository
        class CaptureRepository(DiscoveredCompaniesRepository):
            def search(self, _request, *, offset=0, limit=None):
                self.offset, self.limit = offset, limit
                return []
        repository = CaptureRepository(dsn="")
        DiscoveredCompaniesSearchService(repository).search(
            SearchRequest(industry="Pump", location="Krishnagiri", max_results=7)
        )
        self.assertEqual(repository.limit, 70)


if __name__ == "__main__":
    unittest.main()
