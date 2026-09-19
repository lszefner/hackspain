from __future__ import annotations

import math
from urllib.parse import urlsplit

import httpx

from ingestion.contracts import canonical_bytes

from .contextual_contracts import ReviewInputError, ReviewProviderError, strict_json
from .contextual_review import USAGE_KEYS, ProviderReply


class DeepSeekReviewProvider:
    provider = "deepseek"

    def __init__(self, *, endpoint: str, model: str, api_key: str,
                 timeout_seconds: float = 90.0, transport=None):
        parsed = urlsplit(endpoint)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or not isinstance(model, str) or not model.strip()
                or not isinstance(api_key, str) or not api_key.strip()
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ReviewInputError("invalid_provider_configuration")
        self.endpoint, self.model, self.api_key = endpoint, model, api_key
        self.timeout_seconds, self.transport = timeout_seconds, transport

    async def review(self, request: dict) -> ProviderReply:
        user = {key: value for key, value in request.items() if key != "system_prompt"}
        body = {"model": self.model, "messages": [
            {"role": "system", "content": request["system_prompt"]},
            {"role": "user", "content": canonical_bytes(user).decode("utf-8")},
        ], "temperature": 0, "response_format": {"type": "json_object"}, "max_tokens": 8192}
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport,
                                  follow_redirects=False) as client,
                client.stream("POST", self.endpoint, json=body, headers={
                    "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                }) as response,
            ):
                if not 200 <= response.status_code < 300:
                    code = ("authentication_error" if response.status_code in (401, 403)
                            else "rate_limited" if response.status_code == 429 else "http_error")
                    raise ReviewProviderError(code)
                chunks = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    if len(chunks) + len(chunk) > 100_000:
                        raise ReviewProviderError("output_too_large")
                    chunks.extend(chunk)
                request_id = response.headers.get("x-request-id") or None
            raw = strict_json(bytes(chunks))
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(content, str):
                raise ReviewProviderError("invalid_response")
            payload = strict_json(content)
            if not isinstance(payload, dict) or not isinstance(raw.get("model"), str) or not raw["model"]:
                raise ReviewProviderError("invalid_response")
            usage = raw.get("usage")
            return ProviderReply(payload, raw["model"], request_id, {
                key: value for key, value in (usage if isinstance(usage, dict) else {}).items()
                if key in USAGE_KEYS and type(value) is int and value >= 0
            })
        except httpx.TimeoutException as exc:
            raise ReviewProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ReviewProviderError("transport_error") from exc
        except (ValueError, UnicodeError, KeyError, IndexError, TypeError, RecursionError) as exc:
            raise ReviewProviderError("invalid_response") from exc
