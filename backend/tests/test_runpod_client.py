import unittest
from unittest.mock import patch

from services.llm_services import LLMService, LLMTemporarilyUnavailableError
from services.runpod_client import RunPodClient


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeRunPodClient:
    post_response = None
    status_responses = []
    payloads = []

    def __init__(self, base_url, mode, timeout, logger=None):
        self.mode = mode

    def post(self, payload, headers=None, timeout=None):
        self.payloads.append(payload)
        return self.post_response

    def get_status(self, job_id, headers=None, timeout=None):
        return self.status_responses.pop(0)


class RunPodClientTests(unittest.TestCase):
    def test_sync_requests_keep_the_runsync_endpoint(self):
        client = RunPodClient(base_url="https://api.runpod.ai/v2/endpoint/runsync")

        self.assertEqual(client.base_url, "https://api.runpod.ai/v2/endpoint/runsync")
        self.assertEqual(client.build_request_url(), "https://api.runpod.ai/v2/endpoint/runsync")
        self.assertEqual(client.build_status_url("job-123"), "https://api.runpod.ai/v2/endpoint/status/job-123")

    def test_async_requests_use_status_urls_instead_of_appending_to_runsync(self):
        client = RunPodClient(base_url="https://api.runpod.ai/v2/endpoint/runsync", mode="async")

        self.assertEqual(client.build_request_url(), "https://api.runpod.ai/v2/endpoint/run")
        self.assertEqual(client.build_status_url("job-123"), "https://api.runpod.ai/v2/endpoint/status/job-123")

    def test_extract_text_recurses_into_nested_openai_style_payloads(self):
        payload = {
            "id": "resp_123",
            "output": [
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "text", "text": "hello from nested payload"}
                                ]
                            }
                        }
                    ]
                }
            ],
        }

        self.assertEqual(RunPodClient._extract_text(payload), "hello from nested payload")

    def setUp(self):
        FakeRunPodClient.payloads = []
        FakeRunPodClient.status_responses = []

    @patch("services.llm_services.RunPodClient", FakeRunPodClient)
    def test_sync_request_uses_the_runpod_prompt_input_envelope(self):
        FakeRunPodClient.post_response = FakeResponse(
            200, {"status": "COMPLETED", "output": [{"generated_text": "Hello"}]}
        )
        with patch.object(LLMService, "_build_payload", wraps=LLMService()._build_payload) as build_payload:
            response = LLMService().invoke("System", "User", temperature=0.4, max_tokens=25)

        self.assertEqual(response, "Hello")
        self.assertTrue(build_payload.called)
        self.assertEqual(
            FakeRunPodClient.payloads[0],
            {
                "input": {
                    "prompt": "System\n\nUser: User\n\nAssistant:",
                    "sampling_params": {"temperature": 0.4, "max_tokens": 25},
                }
            },
        )

    def test_messages_mode_uses_native_vllm_chat_shape(self):
        with patch("services.llm_services.settings.RUNPOD_INPUT_MODE", "messages"):
            payload = LLMService()._build_payload("System", "User", 0.4, 25)
        self.assertEqual(
            payload["input"]["messages"],
            [{"role": "system", "content": "System"}, {"role": "user", "content": "User"}],
        )

    @patch("services.llm_services.time.sleep")
    @patch("services.llm_services.RunPodClient", FakeRunPodClient)
    def test_async_polling_retries_202_then_returns_completed_output(self, _sleep):
        FakeRunPodClient.post_response = FakeResponse(200, {"id": "job-1", "status": "IN_QUEUE"})
        FakeRunPodClient.status_responses = [
            FakeResponse(202, {}),
            FakeResponse(200, {"id": "job-1", "status": "COMPLETED", "output": [{"text": "Done"}]}),
        ]
        with patch("services.llm_services.settings.OPENAI_API_BASE_URL", "https://api.runpod.ai/v2/test/run"):
            self.assertEqual(LLMService().invoke("System", "User"), "Done")

    @patch("services.llm_services.RunPodClient", FakeRunPodClient)
    def test_failed_job_is_reported_without_retrying(self):
        FakeRunPodClient.post_response = FakeResponse(
            200, {"id": "job-1", "status": "FAILED", "error": "worker crashed"}
        )
        with self.assertRaisesRegex(LLMTemporarilyUnavailableError, "worker crashed"):
            LLMService().invoke("System", "User")


if __name__ == "__main__":
    unittest.main()
