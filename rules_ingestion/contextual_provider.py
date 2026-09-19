from __future__ import annotations

import math
import os
from urllib.parse import urlsplit

import httpx

from ingestion.contracts import canonical_bytes

from .contextual_contracts import ReviewInputError, ReviewProviderError, strict_json
from .contextual_review import USAGE_KEYS, ProviderReply


def review_provider_from_environment(*, endpoint: str | None = None,
                                    model: str | None = None,
                                    transport=None) -> DeepSeekReviewProvider:
    """The reviewer, configured from the process environment.

    The contextual review is another DeepSeek call on the same Helmcode
    account as extraction and rule authoring, so it defaults to the same
    credentials instead of demanding a parallel set. REVIEW_ENDPOINT,
    REVIEW_MODEL and REVIEW_API_KEY remain as overrides, for running the
    second opinion on a stronger model or a separate account.

    The reviewer's identity is recorded in the run signature and in the review
    record either way, so an implicit default is still auditable after the
    fact -- but note that changing HELMCODE_DEEPSEEK_MODEL then changes the
    reviewer too.
    """
    from . import helmcode

    resolved = {
        "REVIEW_ENDPOINT / HELMCODE_BASE_URL":
            endpoint or os.environ.get("REVIEW_ENDPOINT") or helmcode.chat_endpoint(),
        "REVIEW_MODEL / HELMCODE_DEEPSEEK_MODEL":
            model or os.environ.get("REVIEW_MODEL") or helmcode.model(),
        "REVIEW_API_KEY / HELMCODE_API_KEY":
            os.environ.get("REVIEW_API_KEY") or helmcode.api_key(),
    }
    missing = [name for name, value in resolved.items() if not value]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    values = list(resolved.values())
    return DeepSeekReviewProvider(endpoint=values[0], model=values[1],
                                  api_key=values[2], transport=transport)


class DeepSeekReviewProvider:
    provider = "deepseek"

    def __init__(self, *, endpoint: str, model: str, api_key: str,
                 timeout_seconds: float = 180.0, transport=None):
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
        chunks = bytearray()
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
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    if len(chunks) + len(chunk) > 400_000:
                        raise ReviewProviderError(
                            "output_too_large", raw=bytes(chunks),
                            detail="cap_exceeded")
                    chunks.extend(chunk)
                request_id = response.headers.get("x-request-id") or None
            raw = strict_json(bytes(chunks))
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            if choice.get("finish_reason") != "stop" or not isinstance(content, str):
                raise ReviewProviderError(
                    "invalid_response", raw=bytes(chunks),
                    detail=f"finish_reason={choice.get('finish_reason')}")
            try:
                payload = strict_json(content)
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ReviewProviderError(
                    "invalid_response", raw=bytes(chunks),
                    detail="content_not_json") from exc
            if not isinstance(payload, dict) or not isinstance(raw.get("model"), str) or not raw["model"]:
                raise ReviewProviderError(
                    "invalid_response", raw=bytes(chunks), detail="model_missing")
            usage = raw.get("usage")
            return ProviderReply(payload, raw["model"], request_id, {
                key: value for key, value in (usage if isinstance(usage, dict) else {}).items()
                if key in USAGE_KEYS and type(value) is int and value >= 0
            })
        except httpx.TimeoutException as exc:
            raise ReviewProviderError("timeout") from exc
        except httpx.HTTPError as exc:
            raise ReviewProviderError("transport_error") from exc
        except ReviewProviderError:
            raise
        except (ValueError, UnicodeError, KeyError, IndexError, TypeError, RecursionError) as exc:
            raise ReviewProviderError(
                "invalid_response",
                raw=bytes(chunks) if chunks else None) from exc
