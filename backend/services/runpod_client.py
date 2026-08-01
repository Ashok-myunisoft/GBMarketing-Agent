import json
import logging
import re
from typing import Any, Dict, Optional

import httpx


class RunPodClient:
    """Small wrapper around RunPod serverless endpoints with safe URL construction."""

    def __init__(self, base_url: str, mode: str = "sync", timeout: float = 30.0, logger: Optional[logging.Logger] = None):
        if not base_url:
            raise ValueError("RunPod base URL is required")

        self._base_url = self._normalize_base_url(base_url)
        self.mode = (mode or "sync").lower()
        if self.mode not in {"sync", "async"}:
            raise ValueError(f"Unsupported RunPod mode: {mode}")

        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

    @property
    def base_url(self) -> str:
        return self._base_url

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        url = (base_url or "").strip()
        if not url:
            raise ValueError("RunPod base URL is required")
        return url.rstrip("/")

    def build_request_url(self) -> str:
        if self.mode == "sync":
            return self.base_url

        if self.base_url.endswith("/runsync"):
            return self.base_url[:-8] + "/run"
        if self.base_url.endswith("/run"):
            return self.base_url
        return f"{self.base_url}/run"

    def build_status_url(self, job_id: str) -> str:
        if not job_id:
            raise ValueError("RunPod job id is required")

        base = self.base_url
        if base.endswith("/runsync"):
            base = base[:-8]
        elif base.endswith("/run"):
            base = base[:-4]

        return f"{base}/status/{job_id}"

    def _sanitize_headers(self, headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not headers:
            return {}

        sanitized: Dict[str, Any] = {}
        for key, value in headers.items():
            if key.lower() in {"authorization", "x-api-key", "api-key", "token", "cookie"}:
                sanitized[key] = self._mask_secret(str(value))
            else:
                sanitized[key] = value
        return sanitized

    @staticmethod
    def _mask_secret(value: str) -> str:
        if not value:
            return value
        if value.startswith("Bearer "):
            return f"Bearer {'*' * 8}"
        return f"{'*' * 8}"

    @staticmethod
    def _serialize_payload(payload: Any) -> str:
        if payload is None:
            return "None"
        try:
            return json.dumps(payload, sort_keys=True, default=str)
        except TypeError:
            return str(payload)

    @staticmethod
    def _extract_text(payload: Any) -> Optional[str]:
        skip_keys = {
            "role",
            "name",
            "id",
            "created",
            "object",
            "model",
            "status",
            "type",
            "finish_reason",
            "index",
            "usage",
            "workerid",
            "worker_id",
            "retries",
            "delaytime",
        }

        def visit(value: Any, parent_key: Optional[str] = None) -> Optional[str]:
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    return None
                if parent_key and parent_key.lower() in skip_keys:
                    return None
                lowered = stripped.lower()
                if any(marker in lowered for marker in ("timed out", "retry", "retries", "in progress", "pending", "starting", "running", "failed", "error", "cancelled", "worker")):
                    return None
                if re.fullmatch(r"[a-z0-9_-]{10,}", lowered) and " " not in stripped:
                    return None
                return stripped

            if isinstance(value, list):
                rendered = []
                for item in value:
                    candidate = visit(item)
                    if candidate:
                        rendered.append(candidate)
                return "".join(rendered) or None

            if isinstance(value, dict):
                for key in ("output", "response", "result", "text", "output_text", "content"):
                    if key in value:
                        candidate = visit(value[key], key)
                        if candidate is not None:
                            return candidate

                if "choices" in value and value["choices"]:
                    for choice in value["choices"]:
                        candidate = visit(choice)
                        if candidate is not None:
                            return candidate

                for key, nested_value in value.items():
                    if key.lower() in skip_keys:
                        continue
                    candidate = visit(nested_value, key)
                    if candidate is not None:
                        return candidate

            return None

        return visit(payload)

    def _log_request(self, method: str, url: str, headers: Optional[Dict[str, Any]], payload: Any) -> None:
        self.logger.debug(
            "RunPod request method=%s url=%s headers=%s payload=%s",
            method,
            url,
            self._sanitize_headers(headers),
            self._serialize_payload(payload),
        )

    def _log_response(self, method: str, url: str, response: httpx.Response) -> None:
        body = response.text
        if body and len(body) > 4000:
            body = body[:4000] + "..."
        self.logger.debug(
            "RunPod response method=%s url=%s status=%s body=%s",
            method,
            url,
            response.status_code,
            body,
        )

    def request(self, method: str, url: str, *, headers: Optional[Dict[str, Any]] = None, payload: Any = None, timeout: Optional[float] = None) -> httpx.Response:
        effective_timeout = timeout if timeout is not None else self.timeout
        request_kwargs: Dict[str, Any] = {"headers": headers or {}}
        if payload is not None:
            request_kwargs["json"] = payload

        self._log_request(method, url, headers, payload)
        with httpx.Client(timeout=effective_timeout) as client:
            response = client.request(method, url, **request_kwargs)
        self._log_response(method, url, response)
        return response

    def post(self, payload: Any, headers: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> httpx.Response:
        url = self.build_request_url()
        return self.request("POST", url, headers=headers, payload=payload, timeout=timeout)

    def get_status(self, job_id: str, headers: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> httpx.Response:
        url = self.build_status_url(job_id)
        return self.request("GET", url, headers=headers, payload=None, timeout=timeout)
