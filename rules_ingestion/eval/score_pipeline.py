#!/usr/bin/env python3
"""score_pipeline.py · run full pipeline against eval fixtures and report.

    # offline (lexical, no API keys) — default CI mode
    python3 -m rules_ingestion.eval.score_pipeline

    # live JEV + DeepSeek
    python3 -m rules_ingestion.eval.score_pipeline --live

Outputs a JSON + human report under rules_ingestion/eval/reports/.
Exit code 0 if all tier pass criteria are met, 1 otherwise.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from rules_ingestion.classify import (
    IS_RULE_HI, classify_lines,
)
from rules_ingestion.codegen import validate_source, enrich_new_rules
from rules_ingestion import loader, store
from rules_ingestion.merge import build_merged
from rules_ingestion.eval import fixtures_dir, reports_dir

_HERE = os.path.dirname(os.path.abspath(__file__))
_RI = os.path.dirname(_HERE)  # rules_ingestion/
_FIXTURES = fixtures_dir()
_REPORTS = reports_dir()


def _load_csv(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _as_bool01(val: Any) -> int:
    if val in (1, "1", True, "true", "True"):
        return 1
    return 0


def _threshold_is_rule(score: float, expected: int, floor: float) -> bool:
    """Does the predicted is_rule agree with expected binary label?"""
    if expected == 1:
        # Clear the row's floor (usually the ACTIVATE band); tiny float slack.
        return score + 1e-6 >= float(floor)
    # Noise must not clear the high gate (uncertain band is still "not a rule").
    return score < IS_RULE_HI


def _classify_rows(rows: List[dict], live: bool) -> List[Tuple[dict, Any]]:
    lines = [{"text": r["text"], "declared": False, "id": r.get("id")} for r in rows]
    return classify_lines(lines, prefer_jev=live, use_llm=live)


def score_labeled_csv(path: str, live: bool = False) -> dict:
    """Score baseline or traps CSV with ground-truth columns."""
    rows = _load_csv(path)
    # Offline: skip rows that need JEV/LLM (e.g. English paraphrases).
    if not live:
        rows = [r for r in rows if not _as_bool01(r.get("requires_llm"))]
    classified = _classify_rows(rows, live=live)

    results = []
    is_rule_hits = maps_hits = 0
    by_trap: Dict[str, dict] = {}

    for row, (_line, res) in zip(rows, classified):
        exp_is = _as_bool01(row.get("expected_is_rule"))
        exp_map = (row.get("expected_maps_to") or "NONE").upper()
        floor = float(row.get("is_rule_floor") or 0.0)
        ok_is = _threshold_is_rule(res.is_rule, exp_is, floor)
        # Noise: must not ACTIVATE; is_rule gate is the primary signal.
        if exp_is == 0:
            ok_map = res.route != "ACTIVATE"
        elif exp_map == "NONE":
            # Novel rules: must NOT force a false canonical ACTIVATE.
            # NEW_RULE / LLM_FALLBACK / NOT_A_RULE with leftover maps_to is OK offline.
            ok_map = res.route != "ACTIVATE"
        else:
            ok_map = res.maps_to == exp_map

        if ok_is:
            is_rule_hits += 1
        if ok_map:
            maps_hits += 1

        entry = {
            "id": row.get("id"),
            "trap_type": row.get("trap_type") or None,
            "text": row["text"][:120],
            "expected_is_rule": exp_is,
            "actual_is_rule": res.is_rule,
            "ok_is_rule": ok_is,
            "expected_maps_to": exp_map,
            "actual_maps_to": res.maps_to,
            "actual_maps_to_conf": res.maps_to_conf,
            "actual_route": res.route,
            "actual_method": res.method,
            "ok_maps_to": ok_map,
            "mismatch": (not ok_map) or (not ok_is),
        }

        # Optional codegen sandbox bait check (offline AST only)
        if _as_bool01(row.get("codegen_must_reject_unsafe")):
            # Deliberately unsafe stub the safety gate must reject if someone
            # tried to emit it — we also try compile_new_rule offline.
            bait = (
                "def check(inv, master, params):\n"
                "    import os\n"
                "    return eval('1'), 'x'\n"
            )
            ok_ast, _why = validate_source(bait)
            entry["codegen_unsafe_rejected"] = (not ok_ast)
            if ok_ast:
                entry["mismatch"] = True

        results.append(entry)

        trap = row.get("trap_type") or "_none"
        bucket = by_trap.setdefault(trap, {"n": 0, "is_ok": 0, "map_ok": 0, "failures": []})
        bucket["n"] += 1
        bucket["is_ok"] += int(ok_is)
        bucket["map_ok"] += int(ok_map)
        if not ok_map or not ok_is:
            bucket["failures"].append({
                "id": row.get("id"),
                "expected_maps_to": exp_map,
                "actual_maps_to": res.maps_to,
                "expected_is_rule": exp_is,
                "actual_is_rule": res.is_rule,
                "route": res.route,
            })

    n = max(len(rows), 1)
    return {
        "file": os.path.basename(path),
        "n": len(rows),
        "is_rule_accuracy": round(is_rule_hits / n, 4),
        "maps_to_accuracy": round(maps_hits / n, 4),
        "by_trap_type": {
            k: {
                "n": v["n"],
                "is_rule_pass_rate": round(v["is_ok"] / max(v["n"], 1), 4),
                "maps_to_pass_rate": round(v["map_ok"] / max(v["n"], 1), 4),
                "failures": v["failures"],
            }
            for k, v in by_trap.items()
        },
        "mismatches": [r for r in results if r["mismatch"]],
        "results": results,
    }


def score_structural(xlsx_path: str, manifest: dict, live: bool = False) -> dict:
    """Extraction recall + maps_to precision on recalled embedded rules."""
    from rules_ingestion.loader import scan_candidates

    folder = os.path.dirname(xlsx_path)
    # scan_candidates walks the whole folder — isolate via temp copy name filter
    # by scanning only this file through a tiny temp dir.
    with tempfile.TemporaryDirectory() as tmp:
        import shutil
        dest = os.path.join(tmp, os.path.basename(xlsx_path))
        shutil.copy2(xlsx_path, dest)
        candidates = scan_candidates(tmp)

    texts = [c["text"] for c in candidates]
    gt = manifest["structural_ground_truth"]["embedded_rules"]
    must_not = manifest["structural_ground_truth"]["must_not_activate_substrings"]

    recalled = []
    missed = []
    for rule in gt:
        needle = rule["must_contain"].lower()
        hit = next((t for t in texts if needle in t.lower()), None)
        if hit is None:
            # softer: all significant tokens
            toks = [w for w in needle.split() if len(w) > 3]
            hit = next((t for t in texts if sum(1 for w in toks if w in t.lower()) >= max(2, len(toks) - 1)), None)
        if hit:
            recalled.append({**rule, "extracted_text": hit[:160]})
        else:
            missed.append(rule)

    # Classify recalled texts
    class_rows = [{"text": r["extracted_text"], "declared": False} for r in recalled]
    classified = classify_lines(class_rows, prefer_jev=live, use_llm=live) if class_rows else []

    map_ok = 0
    map_details = []
    for rule, (_line, res) in zip(recalled, classified):
        exp = rule["expected_maps_to"]
        ok = res.maps_to == exp or (exp == "NONE" and res.route == "NEW_RULE")
        if rule.get("expected_is_rule") == 1 and res.route == "NOT_A_RULE":
            ok = False
        if ok:
            map_ok += 1
        map_details.append({
            "id": rule["id"],
            "expected_maps_to": exp,
            "actual_maps_to": res.maps_to,
            "actual_route": res.route,
            "ok": ok,
            "text": rule["extracted_text"][:100],
        })

    # Domain-boundary: lookalike-only cells must not ACTIVATE as payment rules.
    # Skip cells that also contain a real embedded rule (e.g. IBAN rule + cafe noise
    # in the same free-text cell) — those are extraction noise, not lookalike fails.
    rule_needles = [r["must_contain"].lower() for r in gt]
    false_activations = []
    for c, (_line, res) in zip(
        [{"text": t} for t in texts],
        classify_lines([{"text": t, "declared": False} for t in texts],
                       prefer_jev=live, use_llm=live) if texts else [],
    ):
        if res.route != "ACTIVATE":
            continue
        low = c["text"].lower()
        if any(n in low for n in rule_needles):
            continue
        if any(s.lower() in low for s in must_not):
            false_activations.append({
                "text": c["text"][:120],
                "maps_to": res.maps_to,
            })

    n_gt = max(len(gt), 1)
    n_rec = max(len(recalled), 1)
    return {
        "file": os.path.basename(xlsx_path),
        "candidates_extracted": len(candidates),
        "embedded_rules": len(gt),
        "recall": round(len(recalled) / n_gt, 4),
        "precision_on_recalled": round(map_ok / n_rec, 4) if recalled else 0.0,
        "missed": missed,
        "map_details": map_details,
        "false_activations_on_lookalikes": false_activations,
    }


def score_e2e_merge(policy: str = "balanced", live: bool = False) -> dict:
    """Build store from norm + fold fixture discovery → assert merge invariants."""
    cfg = os.path.join(_RI, "sources.yaml")
    profile_path = os.path.join(_RI, "profiles", f"{policy}.yaml")
    load = loader.load(cfg)
    classified = classify_lines(
        load.norma_lines, prefer_jev=live, use_llm=live,
    )
    profile = loader.load_config(profile_path)
    store_doc = store.build_ruleset(load, classified, profile, generated_at="")

    # Discovery over structural fixture only (CSV ground-truth cols are not docs).
    structural_name = "03_structural.xlsx"
    structural_src = os.path.join(_FIXTURES, structural_name)
    discovered_lines = []
    from rules_ingestion.loader import scan_candidates
    if os.path.isfile(structural_src):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            dest = os.path.join(tmp, structural_name)
            shutil.copy2(structural_src, dest)
            discovered_lines = scan_candidates(tmp)
    disc_classified = classify_lines(
        discovered_lines, prefer_jev=live, use_llm=live,
    )
    # Shape like build_rules discovery report
    activate, new_rule, all_entries = [], [], []
    for line, res in disc_classified:
        entry = {**{k: line.get(k) for k in ("file", "sheet", "cell", "text")}, **res.to_dict()}
        all_entries.append(entry)
        if res.route == "ACTIVATE":
            activate.append(entry)
        elif res.route == "NEW_RULE":
            new_rule.append(entry)

    discovered = {
        "scanned_folder": _FIXTURES,
        "candidates": len(discovered_lines),
        "discovered_canonical": activate,
        "discovered_new_rules": new_rule,
        "all": all_entries,
    }
    merged = build_merged(store_doc, discovered)
    enrich_new_rules(merged, use_llm=False, gen_python=False)

    canon_ids = [r["id"] for r in merged["rules"] if r["origin"] == "canonical"]
    dup_ids = len(canon_ids) != len(set(canon_ids))

    # No hallucinated canonical keys outside catalog
    from rules_ingestion.catalog import CATALOG
    bad_canon = [
        r for r in merged["rules"]
        if r.get("canonical") and r["canonical"] not in CATALOG
    ]

    # Demoted deepseek false-canonicals should not attach IRPF to AMOUNT
    amount = next((r for r in merged["rules"] if r.get("canonical") == "AMOUNT"), None)
    irpf_leaked = False
    if amount:
        for ref in amount.get("source_refs") or []:
            q = (ref.get("quote") or "").lower()
            if "irpf" in q or "retencion" in q.replace("ó", "o"):
                irpf_leaked = True

    return {
        "policy": policy,
        "rules_total": len(merged["rules"]),
        "canonical": merged["stats"]["canonical"],
        "metrics_present": bool(merged.get("metrics")),
        "policy_id": merged.get("policy_id"),
        "duplicate_canonical_ids": dup_ids,
        "hallucinated_canonicals": bad_canon,
        "irpf_leaked_into_amount": irpf_leaked,
        "enabled": merged["stats"]["enabled"],
        "ok": (
            not dup_ids
            and not bad_canon
            and not irpf_leaked
            and bool(merged.get("metrics"))
            and merged.get("policy_id") == policy
        ),
    }


def evaluate(fixtures_dir: str, live: bool = False, policy: str = "balanced") -> dict:
    manifest_path = os.path.join(fixtures_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        from rules_ingestion.eval.generate_fixtures import generate
        generate(fixtures_dir)

    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)
    criteria = manifest["pass_criteria"]

    baseline = score_labeled_csv(
        os.path.join(fixtures_dir, manifest["files"]["baseline"]), live=live)
    traps = score_labeled_csv(
        os.path.join(fixtures_dir, manifest["files"]["traps"]), live=live)
    structural = score_structural(
        os.path.join(fixtures_dir, manifest["files"]["structural"]),
        manifest, live=live)
    e2e = score_e2e_merge(policy=policy, live=live)

    # Pass / fail per tier
    def _trap_types_ok() -> Tuple[bool, List[str]]:
        bad = []
        min_rate = criteria["traps"]["min_trap_type_pass_rate"]
        for tname, bucket in traps["by_trap_type"].items():
            if tname == "_none":
                continue
            # Use maps_to pass rate as primary; is_rule also counted
            rate = min(bucket["maps_to_pass_rate"], bucket["is_rule_pass_rate"])
            if rate < min_rate:
                bad.append(f"{tname}={rate:.2f}")
        return (len(bad) == 0), bad

    traps_ok, traps_bad = _trap_types_ok()

    tier_pass = {
        "baseline": (
            baseline["is_rule_accuracy"] >= criteria["baseline"]["min_is_rule_accuracy"]
            and baseline["maps_to_accuracy"] >= criteria["baseline"]["min_maps_to_accuracy"]
        ),
        "traps": (
            traps["is_rule_accuracy"] >= criteria["traps"]["min_is_rule_accuracy"]
            and traps["maps_to_accuracy"] >= criteria["traps"]["min_maps_to_accuracy"]
            and traps_ok
        ),
        "structural": (
            structural["recall"] >= criteria["structural"]["min_recall"]
            and structural["precision_on_recalled"]
            >= criteria["structural"]["min_precision_on_recalled"]
            and len(structural["false_activations_on_lookalikes"])
            <= (
                criteria["structural"].get("max_false_activations_live", 0)
                if live
                else criteria["structural"].get("max_false_activations_offline", 2)
            )
        ),
        "e2e_merge": e2e["ok"],
    }

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "live" if live else "offline_lexical",
        "policy": policy,
        "seed": manifest.get("seed"),
        "pass_criteria": criteria,
        "tier_pass": tier_pass,
        "all_pass": all(tier_pass.values()),
        "baseline": baseline,
        "traps": traps,
        "traps_systematic_failures": traps_bad,
        "structural": structural,
        "e2e_merge": e2e,
    }
    return report


def _print_human(report: dict) -> None:
    print(f"\n=== e2e pipeline score ({report['mode']}) policy={report['policy']} ===")
    for tier, ok in report["tier_pass"].items():
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {tier}")
    b, t, s = report["baseline"], report["traps"], report["structural"]
    print(f"\n  baseline: is_rule={b['is_rule_accuracy']:.0%} maps_to={b['maps_to_accuracy']:.0%} "
          f"(n={b['n']})")
    print(f"  traps:    is_rule={t['is_rule_accuracy']:.0%} maps_to={t['maps_to_accuracy']:.0%} "
          f"(n={t['n']})")
    if report["traps_systematic_failures"]:
        print(f"  traps systematic: {', '.join(report['traps_systematic_failures'])}")
    for tname, bucket in sorted(t["by_trap_type"].items()):
        if tname == "_none":
            continue
        print(f"    · {tname}: maps={bucket['maps_to_pass_rate']:.0%} "
              f"is_rule={bucket['is_rule_pass_rate']:.0%} (n={bucket['n']})")
        for f in bucket["failures"][:3]:
            print(f"        ! {f['id']}: expected {f['expected_maps_to']} "
                  f"got {f['actual_maps_to']} (is_rule={f['actual_is_rule']:.2f})")
    print(f"  structural: recall={s['recall']:.0%} "
          f"precision={s['precision_on_recalled']:.0%} "
          f"candidates={s['candidates_extracted']}")
    if s["missed"]:
        print(f"    missed: {[m['id'] for m in s['missed']]}")
    if s["false_activations_on_lookalikes"]:
        print(f"    false ACTIVATE on lookalikes: {len(s['false_activations_on_lookalikes'])}")
    e = report["e2e_merge"]
    print(f"  e2e_merge: ok={e['ok']} rules={e['rules_total']} "
          f"irpf_leak={e['irpf_leaked_into_amount']} "
          f"metrics={e['metrics_present']}")
    # maps_to mismatch diff (baseline+traps)
    print("\n  maps_to mismatches:")
    shown = 0
    for block in (b, t):
        for m in block["mismatches"]:
            if not m["ok_maps_to"]:
                print(f"    · {m.get('id')}: expected {m['expected_maps_to']} "
                      f"got {m['actual_maps_to']} [{m['actual_route']}] "
                      f"— {m['text'][:70]}")
                shown += 1
                if shown >= 15:
                    break
        if shown >= 15:
            break
    if shown == 0:
        print("    (none)")
    print()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Score full rules pipeline on e2e fixtures.")
    p.add_argument("--fixtures", default=_FIXTURES)
    p.add_argument("--live", action="store_true", help="use JEV + DeepSeek (needs keys)")
    p.add_argument("--policy", default="balanced")
    p.add_argument("--regen", action="store_true", help="regenerate fixtures before scoring")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    if args.regen or not os.path.isfile(os.path.join(args.fixtures, "manifest.json")):
        from rules_ingestion.eval.generate_fixtures import generate
        generate(args.fixtures)

    report = evaluate(args.fixtures, live=args.live, policy=args.policy)
    os.makedirs(_REPORTS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(_REPORTS, f"report_{stamp}_{report['mode']}.json")
    latest = os.path.join(_REPORTS, "latest.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    with open(latest, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    if not args.quiet:
        _print_human(report)
        print(f"· wrote {out_path}")

    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
