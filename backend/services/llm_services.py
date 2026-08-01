import logging
import time
from typing import Any, Optional

import httpx

from core.config import settings
from services.runpod_client import RunPodClient


class LLMTemporarilyUnavailableError(RuntimeError):
    """The upstream model provider did not recover after bounded retries."""


class LLMService:
    """Communicates with a queue-based RunPod LLM endpoint."""

    _PENDING_STATES = {"IN_QUEUE", "IN_PROGRESS", "PENDING", "STARTING", "RUNNING"}
    _FAILED_STATES = {"FAILED", "ERROR", "TIMED_OUT", "TIMEOUT", "CANCELLED"}

    def __init__(self):
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY not found in .env")
        if not settings.OPENAI_API_BASE_URL:
            raise ValueError("OPENAI_API_BASE_URL not found in .env")
        self.model = settings.OPENAI_MODEL

    @staticmethod
    def _job_id(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return (
            payload.get("id")
            or payload.get("run_id")
            or payload.get("run_key")
            or payload.get("job_id")
            or nested.get("id")
            or nested.get("run_id")
        )

    @staticmethod
    def _provider_message(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        for key in ("error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @classmethod
    def _state(cls, payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        value = payload.get("status")
        return value.upper() if isinstance(value, str) else None

    @staticmethod
    def _completion_text(payload: Any) -> Optional[str]:
        """Extract text only from documented/expected LLM output fields."""
        if isinstance(payload, str):
            return payload.strip() or None
        if isinstance(payload, list):
            parts = [LLMService._completion_text(item) for item in payload]
            text = "".join(part for part in parts if part)
            return text or None
        if not isinstance(payload, dict):
            return None

        for key in ("text", "generated_text", "output_text", "response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        choices = payload.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                text = LLMService._completion_text(choice)
                if text:
                    return text

        message = payload.get("message")
        if isinstance(message, dict):
            text = LLMService._completion_text(message)
            if text:
                return text

        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = LLMService._completion_text(content)
            if text:
                return text

        # RunPod puts a worker's result in the top-level output field.
        if "output" in payload:
            return LLMService._completion_text(payload["output"])
        return None

    @classmethod
    def _completed_result(cls, payload: Any) -> Optional[str]:
        state = cls._state(payload)
        if state == "COMPLETED":
            return cls._completion_text(payload)
        # Some custom workers omit status but return an immediate result.
        if state is None:
            return cls._completion_text(payload)
        return None

    def _build_payload(
        self, system_prompt: str, user_prompt: str, temperature: float, max_tokens: int
    ) -> dict[str, Any]:
        # Queue-based RunPod endpoints require every worker argument inside `input`.
        # The configured worker reports "Empty prompt" when given messages, so prompt
        # mode is the safe default for this endpoint. Native vLLM chat workers can
        # opt in with RUNPOD_INPUT_MODE=messages.
        sampling_params = {
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        input_payload: dict[str, Any]
        if settings.RUNPOD_INPUT_MODE == "messages":
            input_payload = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "sampling_params": sampling_params,
            }
        elif settings.RUNPOD_INPUT_MODE == "prompt":
            input_payload = {
                "prompt": f"{system_prompt}\n\nUser: {user_prompt}\n\nAssistant:",
                "sampling_params": sampling_params,
            }
        else:
            raise ValueError("RUNPOD_INPUT_MODE must be 'prompt' or 'messages'.")
        return {
            "input": input_payload
        }

    def _poll_for_result(
        self, client: RunPodClient, job_id: str, headers: dict[str, str], deadline: float
    ) -> str:
        logger = logging.getLogger(__name__)
        delay = float(settings.RUNPOD_POLL_BACKOFF_SECONDS)
        max_delay = float(settings.RUNPOD_POLL_MAX_DELAY_SECONDS)
        attempts = int(settings.RUNPOD_POLL_ATTEMPTS)
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                response = client.get_status(job_id, headers=headers, timeout=remaining)
                if response.status_code == 202:
                    payload: Any = {"status": "IN_PROGRESS"}
                else:
                    response.raise_for_status()
                    payload = response.json()

                state = self._state(payload)
                if state in self._FAILED_STATES:
                    detail = self._provider_message(payload) or state
                    raise LLMTemporarilyUnavailableError(f"RunPod job {job_id} failed: {detail}")

                text = self._completed_result(payload)
                if text:
                    return text
                if state not in self._PENDING_STATES:
                    raise RuntimeError(f"RunPod job {job_id} returned an invalid response state: {state or 'missing'}")
            except LLMTemporarilyUnavailableError:
                raise
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = exc
                logger.warning("RunPod poll failed (attempt %s/%s): %s", attempt, attempts, exc)

            remaining = deadline - time.monotonic()
            if attempt < attempts and remaining > 0:
                sleep_for = min(delay, max_delay, remaining)
                logger.debug("RunPod job %s is pending; sleeping %.1f seconds", job_id, sleep_for)
                time.sleep(sleep_for)
                delay = min(delay * 2, max_delay)

        message = f"RunPod job {job_id} did not complete before the polling deadline."
        raise LLMTemporarilyUnavailableError(message) from last_error

    def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
    ) -> str:
        base_url = settings.OPENAI_API_BASE_URL.rstrip("/")
        mode = "async" if base_url.endswith("/run") else "sync"
        timeout = float(settings.OPENAI_TIMEOUT_SECONDS)
        deadline = time.monotonic() + float(settings.RUNPOD_POLL_TIMEOUT_SECONDS)
        client = RunPodClient(base_url=base_url, mode=mode, timeout=timeout)
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            response = client.post(self._build_payload(system_prompt, user_prompt, temperature, max_tokens), headers, timeout)
            response.raise_for_status()
            payload = response.json()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise LLMTemporarilyUnavailableError("RunPod could not accept the LLM request.") from exc

        state = self._state(payload)
        if state in self._FAILED_STATES:
            detail = self._provider_message(payload) or state
            raise LLMTemporarilyUnavailableError(f"RunPod request failed: {detail}")
        text = self._completed_result(payload)
        if text:
            return text

        job_id = self._job_id(payload)
        if not job_id:
            raise RuntimeError("RunPod response did not include a completed result or job ID.")
        return self._poll_for_result(client, job_id, headers, deadline)
