import unittest

from services.enrichment.company_enrichment import CompanyTavilyEnrichmentService
from services.extractor.schema import ExtractedCompanyRecord


class ToResultContactDesignationTests(unittest.TestCase):
    """A rejected contact_person (e.g. a bare title with no name attached)
    must not also discard a legitimately-extracted designation - the two
    fields are validated and applied to the result independently."""

    def test_name_and_title_are_split_correctly(self):
        record = ExtractedCompanyRecord(
            contact_person="Rajesh Kumar", designation="Managing Director",
        )
        result = CompanyTavilyEnrichmentService._to_result(record)

        self.assertEqual(result.contact_person, "Rajesh Kumar")
        self.assertEqual(result.designation, "Managing Director")

    def test_bare_title_as_contact_person_keeps_designation_not_both(self):
        # The LLM sometimes returns the title itself as contact_person when
        # no name is actually present in the source document.
        record = ExtractedCompanyRecord(
            contact_person="Managing Director", designation="Managing Director",
        )
        result = CompanyTavilyEnrichmentService._to_result(record)

        self.assertIsNone(result.contact_person)
        self.assertEqual(result.designation, "Managing Director")

    def test_rejected_contact_person_with_a_separately_valid_designation(self):
        # contact_person is unusable, but designation was extracted from a
        # different part of the document and is independently valid.
        record = ExtractedCompanyRecord(
            contact_person="Managing Director", designation="Chief Executive Officer",
        )
        result = CompanyTavilyEnrichmentService._to_result(record)

        self.assertIsNone(result.contact_person)
        self.assertEqual(result.designation, "CEO")

    def test_no_designation_at_all_stays_none(self):
        record = ExtractedCompanyRecord(contact_person="Rajesh Kumar", designation=None)
        result = CompanyTavilyEnrichmentService._to_result(record)

        self.assertEqual(result.contact_person, "Rajesh Kumar")
        self.assertIsNone(result.designation)


if __name__ == "__main__":
    unittest.main()
