from __future__ import annotations

import json
import os


def add_commands(commands):
    decide = commands.add_parser(
        "decide",
        help="Deterministic PAGAR / NO PAGAR / ESCALAR over extracted invoices",
    )
    source = decide.add_mutually_exclusive_group(required=True)
    source.add_argument("--export", help="existing ingestion export bundle")
    source.add_argument("--invoices", help="directory of *.invoice.json files")
    decide.add_argument("--input-dir", help="PDF inventory root (with --invoices)")
    decide.add_argument("--rules", required=True, help="frozen ruleset JSON")
    decide.add_argument("--sources", required=True, help="sources.yaml for master data")
    decide.add_argument("--erp-snapshot", help="ERP snapshot JSON (from erp-snapshot)")
    decide.add_argument("--as-of", required=True, help="decision date YYYY-MM-DD")
    decide.add_argument("--output", required=True, help="new decision bundle directory")

    snapshot = commands.add_parser(
        "erp-snapshot", help="Download the ERP asientos into a snapshot JSON"
    )
    snapshot.add_argument("--base-url", required=True)
    snapshot.add_argument("--output", required=True)

    replay = commands.add_parser(
        "replay-decisions",
        help="Re-run a captured decision bundle offline, no XLSX/ERP/API",
    )
    replay.add_argument("--bundle", required=True)
    replay.add_argument("--output", required=True)


def _decide(args) -> dict:
    from .io import decide_files

    return decide_files(
        rules=args.rules,
        sources=args.sources,
        as_of=args.as_of,
        output=args.output,
        export_dir=args.export,
        invoices_dir=args.invoices,
        input_dir=args.input_dir,
        erp_snapshot=args.erp_snapshot,
        schema_dir=args.schema_dir,
    )


def _snapshot(args) -> dict:
    username = os.environ.get("ERP_USER", "alberto")
    password = os.environ.get("ERP_PASSWORD")
    if not password:
        raise ValueError("ERP_PASSWORD environment variable is required")
    from .erp import fetch_snapshot

    snapshot = fetch_snapshot(args.base_url, username=username, password=password)
    path = os.path.abspath(args.output)
    if os.path.exists(path):
        raise ValueError(f"snapshot output already exists: {args.output}")
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return {"output": path, "records": len(snapshot["records"])}


def _replay(args) -> dict:
    from .io import replay_decisions

    return replay_decisions(args.bundle, args.output)


def run(args) -> dict:
    if args.command == "decide":
        return _decide(args)
    if args.command == "erp-snapshot":
        return _snapshot(args)
    if args.command == "replay-decisions":
        return _replay(args)
    raise ValueError(f"unknown decision command {args.command!r}")
