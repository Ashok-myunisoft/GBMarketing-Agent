import unittest

from services.contact_extraction import dedupe
from services.contact_extraction.models import ContactCandidate, PageCategory


def _candidate(name, source="website", source_url="https://example.com"):
    return ContactCandidate(
        name=name,
        raw_title="Managing Director",
        canonical_designation="Managing Director",
        page_category=PageCategory.LEADERSHIP,
        source_url=source_url,
        source=source,
        evidence=[f"Found via {source}"],
    )


class DedupeTests(unittest.TestCase):
    def test_merges_initial_and_full_name_variants(self):
        merged = dedupe.merge(
            [
                _candidate("R Kumar"),
                _candidate("Ravi Kumar"),
                _candidate("R. Kumar"),
                _candidate("Ravi K."),
            ]
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "Ravi Kumar")

    def test_does_not_merge_different_people(self):
        merged = dedupe.merge([_candidate("Ravi Kumar"), _candidate("Rajesh Kumar")])
        self.assertEqual(len(merged), 2)

    def test_merge_unions_sources_and_evidence(self):
        website = _candidate("Ravi Kumar", source="website", source_url="https://example.com/leadership")
        filesure = _candidate("Ravi Kumar", source="filesure", source_url="filesure:U12345")

        merged = dedupe.merge([website, filesure])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].matched_sources, {"website", "filesure"})
        self.assertEqual(len(merged[0].evidence), 2)
        self.assertIn("https://example.com/leadership", merged[0].source_urls)
        self.assertIn("filesure:U12345", merged[0].source_urls)


if __name__ == "__main__":
    unittest.main()
