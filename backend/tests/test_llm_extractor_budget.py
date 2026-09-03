import unittest
from unittest.mock import patch

from services.extractor.llm_extractor import _fit_document_to_prompt_budget


class LlmExtractorBudgetTests(unittest.TestCase):
    def test_large_corpus_keeps_both_ends_within_configured_limit(self):
        document = "A" * 80 + "MIDDLE" * 20 + "Z" * 80
        with patch("services.extractor.llm_extractor.settings.LLM_EXTRACTION_MAX_DOCUMENT_CHARS", 120):
            fitted = _fit_document_to_prompt_budget(document)

        self.assertTrue(fitted.startswith("A"))
        self.assertTrue(fitted.endswith("Z"))
        self.assertIn("omitted for model capacity", fitted)
        self.assertLessEqual(len(fitted), 120)


if __name__ == "__main__":
    unittest.main()
