from __future__ import annotations

import argparse
import asyncio
import json


def parser():
    p = argparse.ArgumentParser(
        prog="invoice-agent", description="Durable, evidence-linked invoice extraction"
    )
    p.add_argument("--schema-dir", default=None)
    commands = p.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--input")
    source.add_argument("--manifest")
    ingest.add_argument(
        "--ocr", choices=["fal-got-v2", "helmcode-vision"], default="helmcode-vision"
    )
    ingest.add_argument(
        "--interpreter", choices=["deepseek", "jev"], default="deepseek"
    )
    ingest.add_argument("--dpi", type=int, choices=[200, 300], default=200)
    ingest.add_argument("--concurrency", type=int, default=2)
    for command in ("status", "resume", "export", "retry"):
        sub = commands.add_parser(command)
        sub.add_argument("--batch", required=True)
        if command == "export":
            sub.add_argument("--output", required=True)
        if command == "retry":
            sub.add_argument(
                "--stage", choices=["reading", "interpretation"], required=True
            )
            selection = sub.add_mutually_exclusive_group(required=True)
            selection.add_argument("--failed-only", action="store_true")
            selection.add_argument(
                "--include-unknown",
                action="store_true",
                help="Explicitly accept possible duplicate billing for unknown calls",
            )
    interpret = commands.add_parser("interpret")
    interpret.add_argument(
        "--reading-run",
        required=True,
        help="Existing batch UUID; reuse its exact persisted readings",
    )
    interpret.add_argument("--interpreter", choices=["deepseek", "jev"], required=True)
    interpret.add_argument(
        "--reading-review",
        help="Source-hashed, explicitly attributed visual review JSON; creates a separate reviewed batch",
    )
    fixture = commands.add_parser(
        "fixture",
        help="Offline synthetic fixture: validate and export, no providers/database",
    )
    fixture.add_argument("--output", required=True)
    commands.add_parser("preflight").add_argument(
        "--interpreter", choices=["deepseek", "jev"], default="deepseek"
    )
    from payments.cli import add_commands

    add_commands(commands)
    return p


def offline_fixture(schema_dir, output):
    from .contracts import Contracts, blank_invoice, canonical_bytes, digest
    from .export import export_bundle
    from .validation import validate_interpretation

    contracts = Contracts(schema_dir)
    reading = {
        "schema_version": "0.1",
        "file_id": "synthetic.pdf",
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "p1-b1",
                        "kind": "other",
                        "text": "Factura 0007\nTotal 12,30 €",
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }
    invoice = blank_invoice("synthetic.pdf")
    invoice.update(document_type="invoice", invoice_number="0007", currency="EUR")
    invoice["totals"]["total"] = "12.30"
    evidence = {
        ptr: [{"page": 1, "reference_ids": ["p1-b1"]}]
        for ptr in ("/document_type", "/invoice_number", "/currency", "/totals/total")
    }
    contracts.validate("reading", reading)
    result = validate_interpretation(invoice, evidence, reading, contracts)
    outcome = {
        "file_id": "synthetic.pdf",
        "status": result["status"],
        "error": None,
        "reading": reading,
        "invoice": invoice,
        "evidence": evidence,
        "checks": result["checks"],
        "coverage": result["coverage"],
        "cost_usd": None,
    }
    return export_bundle(
        output,
        [
            {
                "file_id": "synthetic.pdf",
                "relative_path": "synthetic.pdf",
                "source_sha256": digest(canonical_bytes(reading)),
            }
        ],
        {
            "interpreter": "fixture",
            "version": "alpha-1",
            "synthetic": True,
            "schema_hashes": contracts.hashes,
        },
        [outcome],
        contracts.schemas,
    )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "fixture":
            result = offline_fixture(args.schema_dir, args.output)
        elif args.command in ("decide", "erp-snapshot", "replay-decisions"):
            try:
                from payments.cli import run

                result = run(args)
            except ImportError:
                print(
                    json.dumps(
                        {
                            "error": {
                                "code": "decision_dependencies_missing",
                                "message": "Install invoice-ingestion-agent[decision] to run decision commands",
                            }
                        }
                    )
                )
                return 2
        else:
            from dotenv import load_dotenv

            from .api import execute_command

            load_dotenv(override=False)
            result = asyncio.run(execute_command(args))
        print(json.dumps(result, ensure_ascii=False, default=str, allow_nan=False))
        return 0
    except (ValueError, TypeError, OSError) as exc:
        # Exceptions raised here are application-controlled; never print HTTP/DB exception strings.
        print(
            json.dumps(
                {"error": {"code": "configuration_or_input", "message": str(exc)}}
            )
        )
        return 2
    except ImportError:
        print(
            json.dumps(
                {
                    "error": {
                        "code": "worker_dependencies_missing",
                        "message": "Install invoice-ingestion-agent[worker] to run worker commands",
                    }
                }
            )
        )
        return 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": {
                        "code": type(exc).__name__,
                        "message": "Operation failed; inspect private job attempts for diagnostics",
                    }
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
