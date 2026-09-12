import unittest
from unittest.mock import patch

from services.gst_turnover_enrichment import ai_validator


class BuildUserPromptTests(unittest.TestCase):
    def test_candidate_with_evidence_includes_an_evidence_line(self):
        ranked_values = [
            ("27AAPFU0939F1ZV", 80, ["search"], "Ambica Electro Control GSTIN: 27AAPFU0939F1ZV"),
        ]
        prompt = ai_validator._build_user_prompt("GST Number", "Ambica Electro Control", ranked_values)

        self.assertIn('Evidence: "Ambica Electro Control GSTIN: 27AAPFU0939F1ZV"', prompt)
        self.assertIn("Value: 27AAPFU0939F1ZV", prompt)
        self.assertIn("Confidence: 80", prompt)
        self.assertIn("Sources: search", prompt)

    def test_candidate_with_no_evidence_omits_the_evidence_segment(self):
        ranked_values = [("27AAPFU0939F1ZV", 80, ["search"], "")]
        prompt = ai_validator._build_user_prompt("GST Number", "Ambica Electro Control", ranked_values)

        self.assertNotIn("Evidence:", prompt)

    def test_multiple_candidates_each_render_their_own_evidence(self):
        ranked_values = [
            ("27AAPFU0939F1ZV", 80, ["search"], "Ambica Electro Control GSTIN: 27AAPFU0939F1ZV"),
            ("33AAPFU0939F1Z2", 80, ["search"], "Supplier GSTIN: 33AAPFU0939F1Z2"),
        ]
        prompt = ai_validator._build_user_prompt("GST Number", "Ambica Electro Control", ranked_values)

        self.assertIn("0. Value: 27AAPFU0939F1ZV", prompt)
        self.assertIn('Evidence: "Ambica Electro Control GSTIN: 27AAPFU0939F1ZV"', prompt)
        self.assertIn("1. Value: 33AAPFU0939F1Z2", prompt)
        self.assertIn('Evidence: "Supplier GSTIN: 33AAPFU0939F1Z2"', prompt)


class ValidateTests(unittest.TestCase):
    @patch("services.gst_turnover_enrichment.ai_validator.settings.ENRICHMENT_GST_TURNOVER_AI_VALIDATION", True)
    def test_still_returns_selected_value_with_the_new_4_tuple_shape(self):
        with patch("services.gst_turnover_enrichment.ai_validator.LLMService") as mock_llm_cls:
            mock_llm_cls.return_value.invoke.return_value = '{"selected_index": 1}'
            ranked_values = [
                ("27AAPFU0939F1ZV", 80, ["search"], "Supplier GSTIN: 27AAPFU0939F1ZV"),
                ("33AAPFU0939F1Z2", 80, ["search"], "Ambica Electro Control GSTIN: 33AAPFU0939F1Z2"),
            ]
            result = ai_validator.validate("GST Number", "Ambica Electro Control", ranked_values)

        self.assertEqual(result, "33AAPFU0939F1Z2")

    def test_fewer_than_two_candidates_skips_the_llm_call(self):
        ranked_values = [("27AAPFU0939F1ZV", 80, ["search"], "")]
        self.assertIsNone(ai_validator.validate("GST Number", "Ambica Electro Control", ranked_values))


if __name__ == "__main__":
    unittest.main()
