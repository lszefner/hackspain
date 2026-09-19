"""Explicit, secret-free run configuration; credentials stay in worker memory."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

VERSION = "alpha-4"


def settings(
    interpreter: str, dpi: int = 200, concurrency: int = 2, ocr: str = "helmcode-vision"
) -> dict:
    if ocr not in {"fal-got-v2", "helmcode-vision"}:
        raise ValueError("Unsupported OCR reader")
    if dpi not in (200, 300) or not 1 <= concurrency <= 16:
        raise ValueError("dpi must be 200 or 300; concurrency must be 1..16")
    return {
        "version": VERSION,
        "ocr": ocr,
        "vision_model": os.getenv("HELMCODE_VISION_MODEL", "gemma4"),
        "vision_layout_model": os.getenv("HELMCODE_LAYOUT_MODEL", "qwen3.6"),
        "vision_reader_version": "vision-regions-2",
        "vision_verify": True,
        "vision_stage_timeout": 600,
        "interpreter": interpreter,
        "dpi": dpi,
        "concurrency": concurrency,
        "timeout": 180,
        "max_attempts": 3,
        "generation": 0,
        "fal_model": "fal-ai/got-ocr/v2",
        "fal_base_url": "https://queue.fal.run",
        "helmcode_base_url": os.getenv("HELMCODE_BASE_URL", ""),
        "deepseek_model": os.getenv("HELMCODE_DEEPSEEK_MODEL", ""),
        "jev_model": os.getenv("JEV_MODEL", "jev-latest"),
        "jev_adapter_version": "jev-choice-0.2",
        "jev_max_questions": 12,
        "jev_max_calls": 256,
        "jev_stage_timeout": 600,
        "jev_base_url": "https://api.typesafe.ai/v1",
        "max_input_chars": int(os.getenv("INGESTION_MAX_INPUT_CHARS", "100000")),
        "max_output_tokens": int(os.getenv("INGESTION_MAX_OUTPUT_TOKENS", "8192")),
        "provider_revision": os.getenv("INGESTION_PROVIDER_REVISION") or None,
        "reproducible_provider_revision": bool(
            os.getenv("INGESTION_PROVIDER_REVISION")
        ),
    }


def credentials(config: dict, *, needs_ocr: bool = True) -> dict:
    names = ["SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_DB_URL"]
    if needs_ocr and config.get("ocr", "fal-got-v2") == "fal-got-v2":
        names.append("FAL_KEY")
    names += (
        ["HELMCODE_API_KEY"] if config["interpreter"] == "deepseek" else ["JEV_API_KEY"]
    )
    uses_helmcode = config["interpreter"] == "deepseek" or (
        needs_ocr and config.get("ocr") == "helmcode-vision"
    )
    if uses_helmcode and "HELMCODE_API_KEY" not in names:
        names.append("HELMCODE_API_KEY")
    missing = [name for name in names if not os.getenv(name)]
    if uses_helmcode:
        required = [("HELMCODE_BASE_URL", "helmcode_base_url")]
        if config["interpreter"] == "deepseek":
            required.append(("HELMCODE_DEEPSEEK_MODEL", "deepseek_model"))
        missing += [name for name, key in required if not config.get(key)]
        endpoint = config.get("helmcode_base_url", "")
        if endpoint:
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "HELMCODE_BASE_URL must be HTTPS without credentials/query/fragment"
                )
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    return {name: os.environ[name] for name in names}
