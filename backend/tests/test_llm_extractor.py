import unittest
from unittest.mock import patch

from services.extractor.llm_extractor import extract
from services.extractor.schema import ExtractedCompanyRecord

_GROUNDED_DOCUMENT = (
    "===== LEGAL (https://example.com/legal) =====\n"
    "Registered office: 12 Industrial Estate, Chennai\n"
    "===== LEADERSHIP (https://example.com/leadership) =====\n"
    "R. Kumar, Managing Director\n"
)


class LlmExtractorTests(unittest.TestCase):
    def test_empty_document_short_circuits_without_calling_the_llm(self):
        with patch("services.extractor.llm_extractor.LLMService") as mock_llm_cls:
            result = extract("", "Example Pumps")
            mock_llm_cls.assert_not_called()
            self.assertEqual(result, ExtractedCompanyRecord())

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_valid_json_response_parses_into_a_record(self, mock_llm_cls, _mock_load):
        mock_llm_cls.return_value.invoke.return_value = (
            '{"contact_person": "R. Kumar", "designation": "Managing Director", '
            '"confidence": {"contact_person": 95}, '
            '"evidence": {"contact_person": "R. Kumar, Managing Director"}, '
            '"source_url": {"contact_person": "https://example.com/leadership"}}'
        )
        result = extract(_GROUNDED_DOCUMENT, "Example Pumps")
        self.assertEqual(result.contact_person, "R. Kumar")
        self.assertEqual(result.designation, "Managing Director")
        self.assertEqual(result.confidence_for("contact_person"), 95)
        self.assertEqual(result.evidence["contact_person"], "R. Kumar, Managing Director")
        self.assertEqual(result.source_url["contact_person"], "https://example.com/leadership")

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_code_fenced_response_is_unwrapped(self, mock_llm_cls, _mock_load):
        mock_llm_cls.return_value.invoke.return_value = '```json\n{"contact_person": "R. Kumar"}\n```'
        result = extract(_GROUNDED_DOCUMENT, "Example Pumps")
        self.assertEqual(result.contact_person, "R. Kumar")

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_malformed_json_degrades_to_an_empty_record(self, mock_llm_cls, _mock_load):
        mock_llm_cls.return_value.invoke.return_value = "not json at all"
        result = extract("some document", "Example Pumps")
        self.assertEqual(result, ExtractedCompanyRecord())

    @patch("services.extractor.llm_extractor.PromptService.load", side_effect=FileNotFoundError())
    def test_missing_prompt_degrades_to_an_empty_record_without_raising(self, _mock_load):
        result = extract("some document", "Example Pumps")
        self.assertEqual(result, ExtractedCompanyRecord())

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_fabricated_value_not_present_anywhere_in_the_document_is_discarded(self, mock_llm_cls, _mock_load):
        # Regression: confirmed by hand against a real run - the model
        # returned "Managing Director" as the contact for a document
        # containing no designation word at all. The prompt's "never guess"
        # instruction alone did not stop this - grounding must.
        mock_llm_cls.return_value.invoke.return_value = (
            '{"contact_person": "Managing Director", '
            '"email": "virwadiasteels@gmail.com"}'
        )
        document = (
            "===== HOME (https://virwadiasteels.com) =====\n"
            "Virwadia Steels - stainless steel suppliers in Chennai.\n"
            "===== CONTACT (https://virwadiasteels.com/contact) =====\n"
            "Email us at virwadiasteels@gmail.com\n"
        )
        result = extract(document, "Virwadia Steels")

        self.assertIsNone(result.contact_person)
        # A value that genuinely appears in the document is kept.
        self.assertEqual(result.email, "virwadiasteels@gmail.com")

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_value_grounded_only_via_its_evidence_quote_is_kept(self, mock_llm_cls, _mock_load):
        # The value itself may be reformatted (e.g. whitespace) from the
        # source text - grounding also accepts a value whose *evidence*
        # quote appears verbatim, not just the value string itself.
        mock_llm_cls.return_value.invoke.return_value = (
            '{"address": "12 Industrial Estate", '
            '"evidence": {"address": "Registered office: 12   Industrial Estate, Chennai"}}'
        )
        document = "===== LEGAL =====\nRegistered office: 12   Industrial Estate, Chennai\n"
        result = extract(document, "Example Pumps")
        self.assertEqual(result.address, "12 Industrial Estate")

    @patch("services.extractor.llm_extractor.PromptService.load", return_value="system prompt")
    @patch("services.extractor.llm_extractor.LLMService")
    def test_interpretive_fields_are_not_grounded(self, mock_llm_cls, _mock_load):
        # industry/business_category/city/state/country are allowed to be a
        # reasonable inference from context, not necessarily a literal quote.
        mock_llm_cls.return_value.invoke.return_value = (
            '{"industry": "Steel Manufacturing", "business_category": "Manufacturer", "state": "Tamil Nadu"}'
        )
        document = "===== HOME =====\nWe make stainless steel pipes in Chennai.\n"
        result = extract(document, "Example Pumps")
        self.assertEqual(result.industry, "Steel Manufacturing")
        self.assertEqual(result.business_category, "Manufacturer")
        self.assertEqual(result.state, "Tamil Nadu")


if __name__ == "__main__":
    unittest.main()
