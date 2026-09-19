"""LLM transport for the desk. One bounded call, honestly accounted.

The adapter (ingestion.providers.deepseek.DeepSeek) is used to construct and
validate the endpoint and to reuse its error vocabulary; its .run() is NOT
used -- that path carries the invoice-extraction prompt, not our purposes.
Every HTTP attempt lands in the ledger as LLM_CALL with real usage numbers or
an explicit cost_known=False; nothing is ever estimated.

A module-level circuit breaker trips after 3 consecutive failed complete()
calls and stays degraded for the life of the process (tests use reset()).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import threading
import time
from urllib.parse import urlsplit

import httpx

from ingestion.providers.deepseek import DeepSeek, ProviderError

TIMEOUT = 8
MAX_INPUT_CHARS = 16000
MAX_OUTPUT_TOKENS = 600
BACKOFF_SECONDS = 0.25
BREAKER_THRESHOLD = 3
FORBIDDEN_HOSTS = {"api.deepseek.com"}

_state_lock = threading.Lock()
_call_lock = threading.Lock()
_enabled = True
_failures = 0
_degraded_reason = None


def reset() -> None:
    """Test seam: clear the circuit breaker and re-enable transport."""
    global _failures, _degraded_reason, _enabled
    with _state_lock:
        _failures = 0
        _degraded_reason = None
        _enabled = True


def configure(enabled: bool = True) -> None:
    global _enabled, _failures, _degraded_reason
    with _state_lock:
        _enabled = enabled
        if enabled:
            _failures = 0
            _degraded_reason = None


def _config() -> dict:
    return {
        "helmcode_base_url": os.environ.get("HELMCODE_BASE_URL", ""),
        "deepseek_model": os.environ.get("HELMCODE_DEEPSEEK_MODEL", ""),
        "api_key": os.environ.get("HELMCODE_API_KEY", ""),
        "timeout": TIMEOUT,
        "max_input_chars": MAX_INPUT_CHARS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }


def _endpoint() -> tuple[str | None, str | None, str | None]:
    """(endpoint, model, error_reason). Credentials never leave _config()."""
    cfg = _config()
    if not cfg["helmcode_base_url"] or not cfg["deepseek_model"] \
            or not cfg["api_key"]:
        return None, None, "missing_configuration"
    try:
        adapter = DeepSeek(cfg)
    except ProviderError:
        return None, None, "invalid_configuration"
    try:
        parts = urlsplit(adapter.endpoint)
    except ValueError:
        return None, None, "invalid_endpoint"
    if (parts.scheme != "https" or parts.username or parts.password
            or parts.query or parts.fragment
            or parts.hostname in FORBIDDEN_HOSTS):
        return None, None, "invalid_endpoint"
    return adapter.endpoint, adapter.model, None


def status() -> dict:
    endpoint, model, reason = _endpoint()
    with _state_lock:
        degraded = (not _enabled) or _degraded_reason is not None or reason is not None
        why = (None if _enabled else "disabled") or _degraded_reason or reason
    return {"degraded": degraded, "reason": why,
            "provider": "helmcode" if endpoint else None,
            "model": model}


def _post(endpoint: str, headers: dict, body: dict):
    """One HTTP attempt under a hard total deadline. Monkeypatched in tests."""
    async def bounded():
        async with httpx.AsyncClient(timeout=TIMEOUT,
                                     follow_redirects=False) as client:
            return await asyncio.wait_for(
                client.post(endpoint, headers=headers, json=body),
                timeout=TIMEOUT)
    return asyncio.run(bounded())


def _nonneg(value) -> float | None:
    """A usage/cost number is only real if it is a finite nonnegative number."""
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return float(value)
    return None


def _emit(led, *, purpose, model, file_id, thread_id, attempt, started,
          status_code=None, error_code=None, usage=None, cost=None):
    usage = usage if isinstance(usage, dict) else {}
    if cost is None:
        cost = usage.get("cost_usd", usage.get("cost"))
    cost = _nonneg(cost)
    led.record(
        "LLM_CALL", file_id=file_id, thread_id=thread_id, actor="llm",
        purpose=purpose, provider="helmcode", model=model,
        attempt=attempt, attempts=attempt + 1,
        input_tokens=_nonneg(usage.get("prompt_tokens",
                                       usage.get("input_tokens"))),
        output_tokens=_nonneg(usage.get("completion_tokens",
                                        usage.get("output_tokens"))),
        cost_usd=cost or 0.0, cost_known=cost is not None,
        status="error" if error_code or (status_code or 0) >= 400 else "ok",
        status_code=status_code, error_code=error_code,
        ms=int((time.monotonic() - started) * 1000))


def _failed():
    global _failures, _degraded_reason
    with _state_lock:
        _failures += 1
        if _failures >= BREAKER_THRESHOLD:
            _degraded_reason = "circuit_open"


def _succeeded():
    global _failures
    with _state_lock:
        _failures = 0


def _decode_strict(content: str) -> dict:
    """JSON object only: no duplicate keys, no NaN/Infinity literals."""
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def no_constant(x):
        raise ValueError(f"non-finite literal {x}")

    return json.loads(content, object_pairs_hook=pairs,
                      parse_constant=no_constant)


def complete(led, purpose: str, data: dict, *, file_id=None, thread_id=None) -> dict | None:
    """Strict JSON completion for a contract purpose. None => caller falls back.

    The call lock is non-blocking: a concurrent request gets the fallback
    immediately instead of queueing behind an 8-second window.
    """
    if not _call_lock.acquire(blocking=False):
        return None
    try:
        return _complete(led, purpose, data, file_id=file_id,
                         thread_id=thread_id)
    finally:
        _call_lock.release()


def _complete(led, purpose: str, data: dict, *, file_id=None,
              thread_id=None) -> dict | None:
    from desk import llm_contract

    with _state_lock:
        if not _enabled or _degraded_reason is not None:
            return None
    endpoint, model, _reason = _endpoint()
    if endpoint is None:
        return None
    api_key = _config()["api_key"]

    user = json.dumps(data, ensure_ascii=False)
    if len(user) > MAX_INPUT_CHARS:
        _emit(led, purpose=purpose, model=model, file_id=file_id,
              thread_id=thread_id, attempt=0, started=time.monotonic(),
              error_code="unsupported_size")
        _failed()
        return None

    body = {"model": model,
            "messages": [{"role": "system", "content": llm_contract.PROMPTS[purpose]},
                         {"role": "user", "content": user}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": MAX_OUTPUT_TOKENS}
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}

    for attempt in range(2):
        started = time.monotonic()
        status_code = None
        try:
            response = _post(endpoint, headers, body)
            status_code = getattr(response, "status_code", None)
            if status_code is not None and status_code >= 400:
                code = ("rate_limited" if status_code == 429 else
                        "authentication_error" if status_code in (401, 403) else
                        "provider_http_error")
                _emit(led, purpose=purpose, model=model, file_id=file_id,
                      thread_id=thread_id, attempt=attempt, started=started,
                      status_code=status_code, error_code=code)
                retryable = status_code == 429 or status_code >= 500
                if not retryable or attempt == 1:
                    _failed()
                    return None
                time.sleep(BACKOFF_SECONDS)
                continue
            raw = response.json()
            if not isinstance(raw, dict):
                raise TypeError("non-object response")
        except (ValueError, AttributeError, TypeError):
            _emit(led, purpose=purpose, model=model, file_id=file_id,
                  thread_id=thread_id, attempt=attempt, started=started,
                  status_code=status_code, error_code="invalid_response")
            _failed()
            return None
        except Exception as exc:
            if not isinstance(exc, (httpx.HTTPError, ProviderError,
                                    TimeoutError)):
                raise
            _emit(led, purpose=purpose, model=model, file_id=file_id,
                  thread_id=thread_id, attempt=attempt, started=started,
                  error_code=getattr(exc, "code", "transport_error"))
            retryable = isinstance(exc, (httpx.HTTPError, TimeoutError)) \
                or getattr(exc, "retryable", False)
            if not retryable or attempt == 1:
                _failed()
                return None
            time.sleep(BACKOFF_SECONDS)
            continue

        usage = raw.get("usage")
        cost = raw.get("cost_usd")
        try:
            choice = raw["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("truncated")
            content = choice["message"]["content"]
            parsed = (_decode_strict(content) if isinstance(content, str)
                      else content)
            if not isinstance(parsed, dict):
                raise TypeError("non-object content")
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            _emit(led, purpose=purpose, model=model, file_id=file_id,
                  thread_id=thread_id, attempt=attempt, started=started,
                  status_code=status_code, usage=usage, cost=cost,
                  error_code="invalid_json")
            _failed()
            return None
        valid = llm_contract.validate(purpose, parsed, data)
        _emit(led, purpose=purpose, model=model, file_id=file_id,
              thread_id=thread_id, attempt=attempt, started=started,
              status_code=status_code, usage=usage, cost=cost,
              error_code=None if valid is not None else "invalid_schema")
        if valid is None:
            _failed()
            return None
        _succeeded()
        return valid
    return None
