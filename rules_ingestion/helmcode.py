"""One place that resolves the Helmcode chat provider.

Helmcode is the compute provider for both halves of the engine: it runs the
DeepSeek interpretation of an invoice and the DeepSeek authoring of a rule.
Rule authoring used to read its own `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL`,
which made one provider look like two and meant a correctly configured engine
could still silently skip rule compilation for want of a second key that was
really the same key.

The convention matches ingestion/providers/deepseek.py: HELMCODE_BASE_URL is
the API root (".../v1") and the chat path is appended. Nothing here has a
hardcoded endpoint default -- an unset base means "not configured", never a
guess at someone else's host.
"""
from __future__ import annotations

import os

CHAT_PATH = "chat/completions"


def api_key() -> str | None:
    return os.environ.get("HELMCODE_API_KEY") or None


def chat_endpoint() -> str | None:
    base = (os.environ.get("HELMCODE_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/{CHAT_PATH}" if base else None


def authoring_model() -> str | None:
    """The model that authors rules.

    Defaults to the one used for interpretation; DEEPSEEK_MODEL overrides it
    so rule authoring can run on a cheaper model without a second account.
    """
    return (os.environ.get("DEEPSEEK_MODEL")
            or os.environ.get("HELMCODE_DEEPSEEK_MODEL") or None)


def available() -> bool:
    return all((api_key(), chat_endpoint(), authoring_model()))
