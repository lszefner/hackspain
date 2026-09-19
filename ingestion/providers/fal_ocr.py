"""fal GOT-OCR2 adapter.

The adapter uses fal's documented data-URI input and queue protocol.  Native
responses are returned intact with a convenient ``text`` key; no geometry,
confidence, or semantic table data is invented from fal's string output.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_error",
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


RequestCallback = Callable[..., Any]


def _status(response: Any) -> int | None:
    value = getattr(response, "status_code", None)
    if value is None and isinstance(response, Mapping):
        value = response.get("status_code")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _json(response: Any) -> Any:
    if isinstance(response, Mapping):
        callback_json = response.get("json")
        if callable(callback_json):
            return callback_json()
        return response
    try:
        return response.json()
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProviderError(
            "fal returned a non-JSON response", code="invalid_response"
        ) from exc


def _detail(response: Any) -> str:
    try:
        value = _json(response)
        return json.dumps(value, ensure_ascii=False, default=str)[:500]
    except Exception:
        return str(getattr(response, "text", "provider request failed"))[:500]


async def _call(
    request: RequestCallback,
    method: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> Any:
    try:
        parameters = inspect.signature(request).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "json" in parameters and "body" not in parameters:
        result = request(method=method, url=url, headers=headers, json=body)
    else:
        result = request(method, url, headers, body)
    if inspect.isawaitable(result):
        result = await result
    return result


def _request_id(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("request_id", "requestId", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _native_text(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    if isinstance(payload.get("text"), str):
        return payload["text"]
    outputs = payload.get("outputs")
    if isinstance(outputs, str):
        return outputs
    if isinstance(outputs, list):
        values: list[str] = []
        for value in outputs:
            if isinstance(value, str):
                values.append(value)
            elif isinstance(value, Mapping):
                # Keep this permissive for future fal response revisions while
                # still preserving the unmodified object in the return value.
                item = value.get("text") or value.get("output")
                if isinstance(item, str):
                    values.append(item)
        return "\n".join(values)
    for key in ("output", "result"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


class FalOCR:
    """Async OCR adapter for ``fal-ai/got-ocr/v2``.

    ``on_request_id`` is invoked immediately after a queued request returns an
    ID, before polling.  This gives the caller a durable reconciliation hook.
    Retries belong to the caller; this class performs no hidden resubmissions.
    """

    provider = "fal"
    default_base_url = "https://queue.fal.run"
    default_model = "fal-ai/got-ocr/v2"

    def __init__(
        self,
        settings: Mapping[str, Any] | None = None,
        request: RequestCallback | None = None,
        on_request_id: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = dict(settings or {})
        self.request = request
        self.on_request_id = on_request_id
        self.api_key = self.settings.get("api_key") or self.settings.get("fal_api_key")
        base_url = self.settings.get("fal_base_url")
        model = self.settings.get("fal_model")
        if not base_url or not model:
            raise ProviderError(
                "fal OCR requires explicit fal_base_url and fal_model",
                code="configuration_error",
            )
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model).strip("/")
        self.endpoint = str(
            self.settings.get("fal_endpoint") or f"{self.base_url}/{self.model}"
        )
        self.timeout = float(self.settings.get("timeout", 180))
        self.poll = bool(self.settings.get("fal_poll", self.settings.get("poll", True)))
        self.poll_interval = float(self.settings.get("fal_poll_interval", 0.5))
        self.max_polls = int(self.settings.get("fal_max_polls", 360))

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            scheme = str(self.settings.get("fal_auth_scheme", "Key"))
            headers["Authorization"] = f"{scheme} {self.api_key}"
        return headers

    async def _request(self, method: str, url: str, body: dict[str, Any]) -> Any:
        headers = self._headers()
        if self.request is not None:
            response = await _call(self.request, method, url, headers, body)
        else:
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.request(
                        method, url, headers=headers, json=body
                    )
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "fal transport failed", code="transport_error", retryable=True
                ) from exc
        status = _status(response)
        if status is not None and status >= 400:
            request_id = _request_id(_json(response))
            raise ProviderError(
                f"fal request failed with HTTP {status}",
                code="authentication_error"
                if status in (401, 403)
                else ("rate_limited" if status == 429 else "provider_http_error"),
                retryable=status == 429 or status >= 500,
                status_code=status,
                request_id=request_id,
            )
        return _json(response)

    async def _save_request_id(
        self, request_id: str, payload: Mapping[str, Any]
    ) -> None:
        if self.on_request_id is None:
            return
        try:
            result = self.on_request_id(request_id, dict(payload))
        except TypeError:
            try:
                result = self.on_request_id(request_id)
            except TypeError:
                result = self.on_request_id(
                    request_id=request_id, response=dict(payload)
                )
        if inspect.isawaitable(result):
            await result

    def _status_url(self, request_id: str) -> str:
        return self._status_url_with_metadata(request_id, None)

    def _validate_queue_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ProviderError(
                "fal queue returned an invalid request URL", code="invalid_response"
            )
        allowed = self.settings.get("fal_allowed_hosts")
        if allowed is None:
            base_host = urlparse(self.base_url).hostname
            allowed = [base_host] if base_host else []
        if parsed.hostname not in set(str(value) for value in allowed if value):
            raise ProviderError(
                "fal queue returned a request URL on an unconfigured host",
                code="invalid_response",
            )
        return url

    def _status_url_with_metadata(
        self, request_id: str, metadata: Mapping[str, Any] | None
    ) -> str:
        if metadata and metadata.get("status_url"):
            return self._validate_queue_url(str(metadata["status_url"]))
        configured = self.settings.get("fal_status_endpoint")
        if configured:
            return self._validate_queue_url(
                str(configured).format(request_id=request_id, model=self.model)
            )
        return f"{self.base_url}/{self.model}/requests/{request_id}/status"

    def _result_url(self, request_id: str) -> str:
        return self._result_url_with_metadata(request_id, None)

    def _result_url_with_metadata(
        self, request_id: str, metadata: Mapping[str, Any] | None
    ) -> str:
        if metadata and metadata.get("response_url"):
            return self._validate_queue_url(str(metadata["response_url"]))
        configured = self.settings.get("fal_result_endpoint")
        if configured:
            return self._validate_queue_url(
                str(configured).format(request_id=request_id, model=self.model)
            )
        return f"{self.base_url}/{self.model}/requests/{request_id}"

    async def resume(
        self, request_id: str, metadata: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Poll a previously submitted request without submitting it again."""

        if not request_id:
            raise ProviderError(
                "fal resume requires a request ID", code="invalid_input"
            )
        for attempt in range(self.max_polls):
            status_payload = await self._request(
                "GET", self._status_url_with_metadata(request_id, metadata), {}
            )
            status = (
                str(status_payload.get("status", "")).upper()
                if isinstance(status_payload, Mapping)
                else ""
            )
            status_text = _native_text(status_payload)
            if status_text is not None:
                result = dict(status_payload)
                result.setdefault("request_id", request_id)
                result["text"] = status_text
                if metadata and isinstance(metadata.get("submission"), Mapping):
                    result["raw_submission"] = dict(metadata["submission"])
                return result
            if status in {"FAILED", "ERROR", "CANCELLED"}:
                raise ProviderError(
                    f"fal OCR request {request_id} ended with status {status}",
                    code="provider_failed",
                    request_id=request_id,
                )
            if status in {"COMPLETED", "SUCCEEDED", "SUCCESS"}:
                result_payload = await self._request(
                    "GET", self._result_url_with_metadata(request_id, metadata), {}
                )
                if (
                    not isinstance(result_payload, Mapping)
                    or _native_text(result_payload) is None
                ):
                    raise ProviderError(
                        "fal completed request contained no OCR output",
                        code="invalid_response",
                        request_id=request_id,
                    )
                result = dict(result_payload)
                result.setdefault("request_id", request_id)
                result["text"] = _native_text(result_payload)
                if metadata and isinstance(metadata.get("submission"), Mapping):
                    result["raw_submission"] = dict(metadata["submission"])
                return result
            if attempt + 1 < self.max_polls and self.poll_interval:
                await asyncio.sleep(self.poll_interval)
        raise ProviderError(
            f"fal OCR request {request_id} did not complete within the polling limit",
            code="poll_timeout",
            retryable=True,
            request_id=request_id,
        )

    async def run(self, image_bytes: bytes) -> dict[str, Any]:
        if not isinstance(image_bytes, bytes) or not image_bytes:
            raise ProviderError(
                "OCR input must be non-empty image bytes", code="invalid_input"
            )
        mime = str(self.settings.get("mime_type", "image/png"))
        encoded = base64.b64encode(image_bytes).decode("ascii")
        body: dict[str, Any] = {
            "input_image_urls": [f"data:{mime};base64,{encoded}"],
            "do_format": bool(self.settings.get("do_format", False)),
        }
        if "multi_page" in self.settings:
            body["multi_page"] = bool(self.settings["multi_page"])
        payload = await self._request("POST", self.endpoint, body)
        if not isinstance(payload, Mapping):
            raise ProviderError(
                "fal returned an invalid submission object", code="invalid_response"
            )
        text = _native_text(payload)
        if text is not None:
            result = dict(payload)
            result["text"] = text
            result.setdefault("raw_submission", dict(payload))
            return result

        request_id = _request_id(payload)
        if not request_id:
            raise ProviderError(
                "fal response contained neither OCR output nor request ID",
                code="invalid_response",
            )
        queue_metadata = {"request_id": request_id, "submission": dict(payload)}
        for source_key, metadata_key in (
            ("status_url", "status_url"),
            ("response_url", "response_url"),
        ):
            value = payload.get(source_key)
            if value:
                queue_metadata[metadata_key] = self._validate_queue_url(str(value))
        await self._save_request_id(request_id, queue_metadata)
        if not self.poll:
            result = dict(payload)
            result.setdefault("text", "")
            result.setdefault("status", "submitted")
            result["raw_submission"] = dict(payload)
            return result

        return await self.resume(request_id, queue_metadata)


FalOCRProvider = FalOCR
FalOCRAdapter = FalOCR
