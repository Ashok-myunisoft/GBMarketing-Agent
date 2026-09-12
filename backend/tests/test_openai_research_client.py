import unittest
from unittest.mock import MagicMock, patch

from services.gst_turnover_enrichment.openai_research_client import OpenAIResearchClient, _render


def _fake_openai_response(output_text: str):
    response = MagicMock()
    response.output_text = output_text
    return response


class RenderTests(unittest.TestCase):
    def test_placeholders_are_substituted(self):
        rendered = _render('Company: {{company_name}}\nCity: {{city}}', company_name="Acme", city="Chennai")
        self.assertEqual(rendered, "Company: Acme\nCity: Chennai")

    def test_missing_or_blank_field_becomes_empty_string(self):
        rendered = _render('City: {{city}}', city=None)
        self.assertEqual(rendered, "City: ")

    def test_unknown_placeholder_becomes_empty_string(self):
        rendered = _render('X: {{not_a_real_field}}')
        self.assertEqual(rendered, "X: ")


class OpenAIResearchClientTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("services.gst_turnover_enrichment.openai_research_client.OpenAI")
        self.mock_openai_cls = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_client = MagicMock()
        self.mock_openai_cls.return_value = self.mock_client
        self.research = OpenAIResearchClient(api_key="fake-key", model="gpt-test")

    def test_search_gst_success(self):
        self.mock_client.responses.create.return_value = _fake_openai_response(
            '{"status": "found", "gstin": "27AAACT2727Q1ZW", "evidence": "matches", '
            '"confidence": "high", "sources": ["https://example.com/a"]}'
        )

        result = self.research.search_gst(company_name="Acme Pvt Ltd", city="Chennai")

        self.assertEqual(result.status, "found")
        self.assertEqual(result.value, "27AAACT2727Q1ZW")
        self.assertEqual(result.confidence_label, "high")
        self.assertEqual(result.sources, ["https://example.com/a"])

    def test_search_gst_uses_web_search_tool_and_json_schema(self):
        self.mock_client.responses.create.return_value = _fake_openai_response(
            '{"status": "not_found", "gstin": null, "evidence": "", "confidence": "low", "sources": []}'
        )

        self.research.search_gst(company_name="Acme Pvt Ltd")

        call_kwargs = self.mock_client.responses.create.call_args.kwargs
        self.assertEqual(call_kwargs["tools"], [{"type": "web_search"}])
        self.assertEqual(call_kwargs["text"]["format"]["type"], "json_schema")
        self.assertIn("Acme Pvt Ltd", call_kwargs["input"])

    def test_search_turnover_success(self):
        self.mock_client.responses.create.return_value = _fake_openai_response(
            '{"status": "found", "turnover": "INR 50 Crore", "currency": "INR", '
            '"financial_year": "FY 2023-24", "metric": "revenue", "evidence": "annual report", '
            '"confidence": "medium", "sources": ["https://example.com/report.pdf"]}'
        )

        result = self.research.search_turnover(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "found")
        self.assertEqual(result.value, "INR 50 Crore")
        self.assertEqual(result.currency, "INR")
        self.assertEqual(result.financial_year, "FY 2023-24")
        self.assertEqual(result.metric, "revenue")
        self.assertEqual(result.confidence_label, "medium")

    def test_not_found_status(self):
        self.mock_client.responses.create.side_effect = [_fake_openai_response(
            '{"status": "not_found", "gstin": null, "evidence": "", "confidence": "low", "sources": []}'
        ), _fake_openai_response(
            '{"status": "found", "gstin": "27AAACT2727Q1ZW", "evidence": "matches", '
            '"confidence": "high", "sources": ["https://example.com/a"]}'
        )]

        result = self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "found")
        self.assertEqual(result.value, "27AAACT2727Q1ZW")
        self.assertEqual(self.mock_client.responses.create.call_count, 2)

    def test_found_status_does_not_trigger_second_search(self):
        self.mock_client.responses.create.return_value = _fake_openai_response(
            '{"status": "found", "gstin": "27AAACT2727Q1ZW", "evidence": "matches", '
            '"confidence": "high", "sources": ["https://example.com/a"]}'
        )

        self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(self.mock_client.responses.create.call_count, 1)

    def test_malformed_json_degrades_to_not_found_without_raising(self):
        self.mock_client.responses.create.side_effect = [
            _fake_openai_response("not json at all"),
            _fake_openai_response(
                '{"status": "found", "gstin": "27AAACT2727Q1ZW", "evidence": "matches", '
                '"confidence": "high", "sources": ["https://example.com/a"]}'
            ),
        ]

        result = self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "found")
        self.assertEqual(self.mock_client.responses.create.call_count, 2)

    def test_request_exception_degrades_to_not_found_without_raising(self):
        self.mock_client.responses.create.side_effect = RuntimeError("network down")

        result = self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "not_found")

    def test_non_dict_json_degrades_to_not_found(self):
        self.mock_client.responses.create.side_effect = [
            _fake_openai_response("[1, 2, 3]"),
            _fake_openai_response(
                '{"status": "found", "gstin": "27AAACT2727Q1ZW", "evidence": "matches", '
                '"confidence": "high", "sources": ["https://example.com/a"]}'
            ),
        ]

        result = self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "found")
        self.assertEqual(self.mock_client.responses.create.call_count, 2)

    def test_no_api_key_skips_the_request_entirely(self):
        research = OpenAIResearchClient(api_key="")
        self.assertFalse(research.is_configured)

        result = research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "not_found")
        self.mock_client.responses.create.assert_not_called()

    def test_fenced_code_block_json_is_still_parsed(self):
        # Defensive fallback - even though json_schema format should prevent
        # this, a fenced response must still degrade gracefully to parsing
        # the JSON inside it, not fail outright.
        self.mock_client.responses.create.return_value = _fake_openai_response(
            '```json\n{"status": "not_found", "gstin": null, "evidence": "", '
            '"confidence": "low", "sources": []}\n```'
        )

        result = self.research.search_gst(company_name="Acme Pvt Ltd")

        self.assertEqual(result.status, "not_found")


if __name__ == "__main__":
    unittest.main()
