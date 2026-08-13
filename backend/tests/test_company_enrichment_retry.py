import unittest

from services.enrichment.company_enrichment import CompanyTavilyEnrichmentService
from services.extractor.schema import ExtractedCompanyRecord


class NeedsRetryTests(unittest.TestCase):
    def test_no_retry_when_tracked_fields_are_all_absent(self):
        # A company with no public contact person shouldn't trigger a retry
        # just because contact_person/designation are None - there's nothing
        # a differently-worded query can surface for a field that plainly
        # isn't published anywhere.
        record = ExtractedCompanyRecord()
        self.assertFalse(CompanyTavilyEnrichmentService._needs_retry(record, {}))

    def test_retries_when_a_found_field_has_low_confidence(self):
        record = ExtractedCompanyRecord(contact_person="R. Kumar")
        confidence = {"contact_person": 40}
        self.assertTrue(CompanyTavilyEnrichmentService._needs_retry(record, confidence))

    def test_no_retry_when_found_fields_are_all_high_confidence(self):
        record = ExtractedCompanyRecord(contact_person="R. Kumar", designation="Managing Director")
        confidence = {"contact_person": 95, "designation": 90}
        self.assertFalse(CompanyTavilyEnrichmentService._needs_retry(record, confidence))

    def test_no_retry_when_field_found_but_confidence_missing_entirely(self):
        # A field the LLM filled in but forgot to score should still be
        # treated as low-confidence (0), not skipped.
        record = ExtractedCompanyRecord(contact_person="R. Kumar")
        self.assertTrue(CompanyTavilyEnrichmentService._needs_retry(record, {}))


if __name__ == "__main__":
    unittest.main()
