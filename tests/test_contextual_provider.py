import json

import httpx
import pytest

from ingestion.contextual_prompt import SYSTEM_PROMPT
from rules_ingestion.contextual_contracts import ReviewInputError, ReviewProviderError
from rules_ingestion.contextual_provider import DeepSeekReviewProvider

REQUEST = {"system_prompt": SYSTEM_PROMPT, "response_schema": {"type": "object"},
           "evaluation": {"synthetic": True}, "context": {}, "sources": {},
           "unavailable_sources": [], "unreviewable_sources": []}
PAYLOAD = {"rule_reviews": [], "findings": [], "reviewed_sources": [], "limitations": []}


def provider(handler, **kwargs):
    return DeepSeekReviewProvider(endpoint="https://provider.test/v1/chat/completions", model="explicit-model",
                                  api_key="not-a-real-secret", transport=httpx.MockTransport(handler), **kwargs)


def envelope(content=None, finish_reason="stop"):
    return {"model": "resolved-model", "choices": [{"finish_reason": finish_reason,
            "message": {"content": content if content is not None else json.dumps(PAYLOAD)}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17, "secret": "discard"}}


@pytest.mark.asyncio
async def test_request_and_response_are_explicit_and_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer not-a-real-secret"
        body = json.loads(request.content)
        assert body["model"] == "explicit-model"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 8192
        assert body["response_format"] == {"type": "json_object"}
        assert body["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        user = json.loads(body["messages"][1]["content"])
        assert user["response_schema"] == REQUEST["response_schema"]
        assert "system_prompt" not in user
        return httpx.Response(200, json=envelope(), headers={"x-request-id": "request-1"})

    reply = await provider(handler).review(REQUEST)
    assert len(calls) == 1
    assert reply.payload == PAYLOAD
    assert reply.model == "resolved-model"
    assert reply.request_id == "request-1"
    assert reply.usage == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(401, "authentication_error"), (403, "authentication_error"),
                                          (429, "rate_limited"), (500, "http_error"), (302, "http_error")])
async def test_http_failures_do_not_retry_or_expose_body(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private-error-secret", headers={"location": "https://elsewhere.test"})

    with pytest.raises(ReviewProviderError) as error:
        await provider(handler).review(REQUEST)
    assert error.value.code == code
    assert "private-error-secret" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["not json", "```json\n{}\n```", "[]", "{\"a\": NaN}",
                                      "{\"a\": Infinity}", "{\"a\":1e999}", "{\"a\":1,\"a\":2}"])
async def test_strict_response_json(content):
    with pytest.raises(ReviewProviderError, match="invalid_response"):
        await provider(lambda _: httpx.Response(200, json=envelope(content))).review(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", None])
async def test_noncomplete_generation_fails(reason):
    with pytest.raises(ReviewProviderError, match="invalid_response"):
        await provider(lambda _: httpx.Response(200, json=envelope(finish_reason=reason))).review(REQUEST)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"not json", b'{"choices":[],"choices":[]}', b'[]', b'{}'])
async def test_malformed_envelope(data):
    with pytest.raises(ReviewProviderError, match="invalid_response"):
        await provider(lambda _: httpx.Response(200, content=data)).review(REQUEST)


@pytest.mark.asyncio
async def test_timeout_and_size_limits():
    def timeout(request):
        raise httpx.ReadTimeout("private timeout detail", request=request)

    with pytest.raises(ReviewProviderError, match="timeout"):
        await provider(timeout).review(REQUEST)
    with pytest.raises(ReviewProviderError, match="output_too_large"):
        await provider(lambda _: httpx.Response(200, content=b"x" * 400_001)).review(REQUEST)


@pytest.mark.asyncio
async def test_truncated_generation_carries_raw_body_and_detail():
    def handler(request):
        return httpx.Response(200, json=envelope(
            content='{"rule_reviews": [', finish_reason="length"))

    with pytest.raises(ReviewProviderError) as error:
        await provider(handler).review(REQUEST)
    assert error.value.code == "invalid_response"
    assert error.value.detail == "finish_reason=length"
    body = error.value.raw
    assert isinstance(body, bytes)
    assert json.loads(body)["choices"][0]["finish_reason"] == "length"


@pytest.mark.parametrize("endpoint", ["http://provider.test", "https://user:password@provider.test", "https://provider.test?key=secret",
                                       "https://provider.test#fragment", "not-a-url"])
def test_endpoint_must_be_explicit_https_without_credentials(endpoint):
    with pytest.raises(ReviewInputError):
        DeepSeekReviewProvider(endpoint=endpoint, model="model", api_key="key")


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeout(timeout):
    with pytest.raises(ReviewInputError):
        provider(lambda _: None, timeout_seconds=timeout)
