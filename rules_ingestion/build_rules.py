#!/usr/bin/env python3
"""build_rules.py · orchestrate load -> classify -> merge -> store.

    python3 -m rules_ingestion.build_rules
    python3 -m rules_ingestion.build_rules --no-llm --out rules.store.json

Deterministic by construction: JEV resolves the v3 norm with no LLM calls, so
re-running with --no-llm produces byte-identical rules (only generated_at moves).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

from . import loader, store
from .classify import classify_lines

_HERE = os.path.dirname(os.path.abspath(__file__))


def _scan(args, say) -> int:
    """Discovery mode: find rules hidden in a folder of arbitrary CSV/XLSX."""
    import json
    candidates = loader.scan_candidates(args.scan)
    say(f"· scanned '{args.scan}': {len(candidates)} sentence-like candidate(s)")
    tier = "lexical (offline)" if args.no_jev else "JEV System One"
    say(f"· discovering with {tier} (Noul is_rule + Choice maps_to) · "
        f"LLM {'OFF' if args.no_llm else 'ON'}")
    classified = classify_lines(candidates, prefer_jev=not args.no_jev, use_llm=not args.no_llm)

    in_tok = out_tok = 0
    activate, new_rule = [], []
    for line, res in classified:
        loc = f"{line['file']}:{line['sheet'] or '-'}!{line['cell']}"
        if res.route in ("ACTIVATE", "NEW_RULE"):
            marker = "✓ MATCH " if res.route == "ACTIVATE" else "★ NEW   "
            say(f"  {marker} {res.maps_to:<13} is_rule={res.is_rule:.2f} "
                f"conf={res.maps_to_conf:.2f} [{res.method}]  {loc}")
            say(f"           “{line['text'][:90]}”")
            (activate if res.route == "ACTIVATE" else new_rule).append((line, res))
        if res.usage:
            in_tok += res.usage.get("input_tokens", 0)
            out_tok += res.usage.get("output_tokens", 0)

    report = {
        "scanned_folder": args.scan,
        "candidates": len(candidates),
        "discovered_canonical": [
            {**{k: l[k] for k in ("file", "sheet", "cell", "text")}, **r.to_dict()}
            for l, r in activate],
        "discovered_new_rules": [
            {**{k: l[k] for k in ("file", "sheet", "cell", "text")}, **r.to_dict()}
            for l, r in new_rule],
        "all": [{**{k: l[k] for k in ("file", "sheet", "cell", "text")}, **r.to_dict()}
                for l, r in classified],
    }
    out_path = os.path.join(os.path.dirname(args.scan.rstrip("/")) or ".", "rules.discovered.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    say(f"· {len(activate)} canonical match(es), {len(new_rule)} NEW rule(s) "
        f"out of {len(candidates)} candidates -> {os.path.relpath(out_path)}")
    if in_tok or out_tok:
        say(f"  JEV usage: {in_tok} in / {out_tok} out tokens")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build Alberto's payment ruleset.")
    p.add_argument("--config", default=os.path.join(_HERE, "sources.yaml"))
    p.add_argument("--profile", default=os.path.join(_HERE, "rules_v3.yaml"))
    p.add_argument("--out", default=os.path.join(_HERE, "rules.store.json"))
    p.add_argument("--no-jev", action="store_true", help="disable JEV API (offline lexical tier)")
    p.add_argument("--no-llm", action="store_true", help="disable LLM fallback")
    p.add_argument("--scan", metavar="FOLDER",
                   help="DISCOVERY: scan a folder of CSV/XLSX for rules hidden in text")
    p.add_argument("--verbose", action="store_true", help="log every JEV/DeepSeek API call live")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    from . import classify as _classify
    _classify.set_debug(args.verbose)

    def say(*a):
        if not args.quiet:
            print(*a)

    if args.scan:
        return _scan(args, say)

    say("· loading sources per", os.path.relpath(args.config))
    load = loader.load(args.config)
    for w in load.warnings:
        say("  ! ", w)

    tier = "lexical (offline)" if args.no_jev else "JEV System One"
    say("· classifying", len(load.norma_lines), "norm line(s) via", tier,
        "· LLM fallback", "OFF" if args.no_llm else "ON")
    classified = classify_lines(load.norma_lines, prefer_jev=not args.no_jev,
                                use_llm=not args.no_llm)
    in_tok = out_tok = 0
    for line, res in classified:
        say(f"  {line['cell']:>4}  {res.route:<12} {res.maps_to:<13} "
            f"is_rule={res.is_rule:.2f} maps={res.maps_to_conf:.2f} [{res.method}]")
        if res.usage:
            in_tok += res.usage.get("input_tokens", 0)
            out_tok += res.usage.get("output_tokens", 0)

    profile = loader.load_config(args.profile)
    ruleset = store.build_ruleset(
        load, classified, profile,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )
    store.save(ruleset, args.out)

    s = ruleset["stats"]
    say("· wrote", os.path.relpath(args.out))
    say(f"  rules: {s['rules_total']} ({s['rules_enabled']} enabled, "
        f"{s['rules_backed_by_master']} backed by master) · "
        f"activations: {s['activations']} · flags: {s['flags']}")
    say(f"  master: {ruleset['master_data']['proveedores']} proveedores, "
        f"{ruleset['master_data']['pedidos']} pedidos")
    if in_tok or out_tok:
        say(f"  JEV usage: {in_tok} in / {out_tok} out tokens "
            f"over {len(load.norma_lines)} line(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
