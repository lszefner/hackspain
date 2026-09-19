"""Content-free events; document contents belong only in private artifacts."""

import json
import sys
from datetime import UTC, datetime

_ALLOWED = {
    "request_key",
    "batch_id",
    "input_id",
    "job_id",
    "attempt_id",
    "stage",
    "provider",
    "model",
    "latency_seconds",
    "pages",
    "input_tokens",
    "output_tokens",
    "cache_hit",
    "error_code",
    "cost_usd",
    "state",
    "count",
    "method",
    "http_status",
    "network_seconds",
    "persistence_seconds",
}


def emit(event: str, **fields):
    record = {"event": event, "time": datetime.now(UTC).isoformat()}
    record.update(
        {
            key: str(value) if key.endswith("_id") else value
            for key, value in fields.items()
            if key in _ALLOWED
        }
    )
    print(json.dumps(record, allow_nan=False), file=sys.stderr, flush=True)
