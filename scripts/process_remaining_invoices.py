"""Run the 498 remaining Caja invoices with visible, content-free progress."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.events import emit

SKIP = {"2026-01-08_P001.pdf", "2026-01-11_P007.pdf"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-key", required=True,
                        help="Keep this key to inspect or repeat the same durable intent.")
    parser.add_argument("--input-dir", type=Path, default=Path("caja/facturas"))
    parser.add_argument("--output", type=Path, required=True,
                        help="Save the full result here; keep this file private.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate the file count without importing the backend or calling services.")
    args = parser.parse_args()
    if not 1 <= len(args.request_key) <= 200:
        parser.error("--request-key must contain 1..200 characters")
    files = sorted(p.name for p in args.input_dir.glob("*.pdf")
                   if p.is_file() and p.name not in SKIP)
    if len(files) != 498:
        parser.error(f"Expected 498 invoices, found {len(files)} in {args.input_dir}")
    emit("remaining_invoices_validated", request_key=args.request_key, count=len(files))
    if args.dry_run:
        return 0

    # No dotenv loading. Preserve the requested evaluator-only review policy.
    os.environ["REVISION_REVIEW_ENABLED"] = "false"
    started = time.monotonic()
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(15):
            emit("remaining_invoices_running", request_key=args.request_key,
                 latency_seconds=round(time.monotonic() - started, 1))

    # Reserve the output before any remote work, so a bad path fails early.
    # Exclusive creation also prevents overwriting a previous result.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        args.output.chmod(0o600)
        json.dump({"request_key": args.request_key, "state": "runner_started",
                   "started_at": datetime.now(UTC).isoformat()}, output)
        output.flush()
        worker = threading.Thread(target=heartbeat, daemon=True)
        worker.start()
        emit("remaining_invoices_started", request_key=args.request_key,
             count=len(files), stage="review_disabled")
        try:
            from backend.run_revision import revisar_lote_sync

            result = revisar_lote_sync(
                files, request_key=args.request_key, evaluation_date="issue-date",
                input_dir=args.input_dir,
            )
            serialized = json.dumps(result, ensure_ascii=False, indent=2)
            output.seek(0)
            output.write(serialized + "\n")
            output.truncate()
            output.flush()
            emit("remaining_invoices_finished", request_key=args.request_key,
                 state=result.get("state"),
                 latency_seconds=round(time.monotonic() - started, 1))
            return 0 if result.get("state") == "completed" else 1
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - redact provider errors
            # Provider exceptions can include sensitive bodies: log only type.
            emit("remaining_invoices_stopped", request_key=args.request_key,
                 error_code=type(exc).__name__,
                 latency_seconds=round(time.monotonic() - started, 1))
            print("Inspect the durable run with --status-key before any new attempt.",
                  file=sys.stderr, flush=True)
            return 130 if isinstance(exc, KeyboardInterrupt) else 1
        finally:
            stopped.set()
            worker.join()


if __name__ == "__main__":
    raise SystemExit(main())
