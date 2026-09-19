"""merge.py · deterministic merge of catalog/store + discovery → schema 2.0.

Produces outcome/rules.merged.json:

  1. Start from every canonical rule in rules.store.json (origin=canonical).
  2. Discovery ACTIVATE hits attach as source_refs on that canonical.
     DeepSeek ACTIVATE without lexicon grounding is demoted to NEW_RULE
     (never silently activates a wrong canonical).
  3. Discovery NEW_RULE hits become separate entries (enabled=false until
     a human/codegen step promotes them).
  4. Dedup by (file, sheet, cell, text).

No LLM calls. Same inputs → same merged JSON (run_id / generated_at aside).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .catalog import CATALOG, lexicon_grounded

MERGED_SCHEMA_VERSION = "2.0"


def _ref_key(ref: dict) -> Tuple:
    return (
        ref.get("file") or "",
        ref.get("sheet") or "",
        ref.get("cell") or "",
        (ref.get("quote") or ref.get("text") or "").strip(),
    )


def _source_ref(entry: dict) -> dict:
    return {
        "file": entry.get("file") or "",
        "sheet": entry.get("sheet") or "",
        "cell": entry.get("cell") or "",
        "quote": entry.get("text") or "",
    }


def _origin_for_method(method: str) -> str:
    m = (method or "").lower()
    if "deepseek" in m or m == "llm":
        return "deepseek_fallback"
    if m in ("jev", "lexical", "jev_lexical_degraded") or not m:
        return "agent"
    return "agent"


def _condition_from_store_rule(rule: dict) -> Optional[dict]:
    logic = rule.get("logic")
    if not logic:
        return None
    if logic.get("status") == "no_code":
        return {"kind": "pending", "note": logic.get("note")}
    if logic.get("function"):
        return {
            "kind": "python_check",
            "module": logic.get("module", "rules_ingestion.checks"),
            "function": logic["function"],
            "source": logic.get("source"),
        }
    return {"kind": "raw", "logic": logic}


def _canonical_entry(store_rule: dict) -> dict:
    trace = store_rule.get("trace") or {}
    refs: List[dict] = []
    seen = set()
    for m in trace.get("matched_from") or []:
        ref = {
            "file": m.get("file") or "FINAL_v7_DEFINITIVO_ahorasi.xlsx",
            "sheet": m.get("sheet") or "",
            "cell": m.get("cell") or "",
            "quote": m.get("text") or "",
            "method": m.get("method") or "jev",
            "is_rule": m.get("is_rule_conf"),
            "maps_to_conf": m.get("maps_to_conf"),
        }
        k = _ref_key(ref)
        if k not in seen:
            seen.add(k)
            refs.append(ref)

    methods = {r.get("method") for r in refs if r.get("method")}
    return {
        "id": store_rule["rule_id"],
        "origin": "canonical",
        "canonical": store_rule["canonical"],
        "title": store_rule.get("title"),
        "text": store_rule.get("description"),
        "source_refs": refs,
        "method": sorted(methods)[0] if len(methods) == 1 else (
            "mixed" if methods else "predefined"
        ),
        "judge": {
            "is_rule": trace.get("is_rule_conf"),
            "maps_to_conf": trace.get("maps_to_conf"),
            "maps_to": store_rule["canonical"] if refs else None,
        },
        "enabled": bool(store_rule.get("enabled", True)),
        # Which norm line set which parameter, kept on the merged rule: the
        # ruleset is the artifact the evaluator and the reviewer read, so the
        # provenance of a threshold has to survive the merge.
        "sheet_params": list(trace.get("params_from_sheet") or []),
        "param_conflicts": list(trace.get("param_conflicts") or []),
        "enabled_by_sheet": bool(trace.get("enabled_by_sheet")),
        "on_fail": store_rule.get("on_fail"),
        "soft_verdict": store_rule.get("soft_verdict"),
        "input_fields": list(store_rule.get("input_fields") or []),
        "params": dict(store_rule.get("params") or {}),
        "condition": _condition_from_store_rule(store_rule),
        "demoted": False,
        "demote_reason": None,
    }


def _new_rule_entry(entry: dict, *, demoted: bool = False,
                    demote_reason: Optional[str] = None,
                    new_index: int = 1) -> dict:
    method = entry.get("method") or "jev"
    slug = hashlib.sha1(
        (entry.get("text") or "").encode("utf-8")
    ).hexdigest()[:8]
    logic = entry.get("logic") or {}
    # Prefer a condition compiled at discovery time; else derive from logic.
    condition = entry.get("compiled_condition")
    if condition is None:
        if logic.get("status") == "no_code":
            condition = {"kind": "pending", "note": logic.get("note")}
        elif logic.get("function") or logic.get("source"):
            condition = {
                "kind": "python_check",
                "module": logic.get("module"),
                "function": logic.get("function"),
                "source": logic.get("source"),
                "valid": logic.get("valid"),
            }
    params = dict(entry.get("compiled_params") or {})
    on_fail = entry.get("compiled_on_fail") or "ESCALAR"
    return {
        "id": f"NEW_{new_index:03d}_{slug}",
        "origin": _origin_for_method(method),
        "canonical": None,
        "title": None,
        "text": entry.get("text") or "",
        "source_refs": [_source_ref(entry)],
        "method": method,
        "judge": {
            "is_rule": entry.get("is_rule"),
            "maps_to_conf": entry.get("maps_to_conf"),
            "maps_to": entry.get("maps_to"),
        },
        "enabled": False,
        "on_fail": on_fail,
        "soft_verdict": "NEEDS_REVIEW",
        "input_fields": [],
        "params": params,
        "condition": condition,
        "demoted": demoted,
        "demote_reason": demote_reason,
    }


def _iter_discovery(discovered: Optional[dict]) -> Iterable[dict]:
    if not discovered:
        return []
    # `all` is the full scan trail (includes NOT_A_RULE). Prefer it when present.
    if discovered.get("all"):
        return list(discovered["all"])
    rows: List[dict] = []
    rows.extend(discovered.get("discovered_canonical") or [])
    rows.extend(discovered.get("discovered_new_rules") or [])
    return rows


def build_merged(store: dict,
                 discovered: Optional[dict] = None,
                 run_id: Optional[str] = None,
                 generated_at: Optional[str] = None) -> dict:
    """Merge store + optional discovery into schema 2.0. Pure / deterministic."""
    generated_at = generated_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    run_id = run_id or generated_at

    by_canonical: Dict[str, dict] = {}
    rules: List[dict] = []
    for sr in store.get("rules") or []:
        entry = _canonical_entry(sr)
        by_canonical[entry["canonical"]] = entry
        rules.append(entry)

    seen_refs: Dict[str, set] = {
        key: {_ref_key(r) for r in entry["source_refs"]}
        for key, entry in by_canonical.items()
    }
    # map id -> set for NEW dedup
    seen_new: set = set()
    rejected: List[dict] = []
    demoted_count = 0
    new_index = 0

    for entry in _iter_discovery(discovered):
        route = entry.get("route")
        text = entry.get("text") or ""
        maps_to = entry.get("maps_to")
        method = entry.get("method") or ""
        ref = _source_ref(entry)
        ref_k = _ref_key(ref)

        if route == "NOT_A_RULE":
            rejected.append({
                **ref,
                "route": route,
                "is_rule": entry.get("is_rule"),
                "method": method,
                "reason": entry.get("reason"),
            })
            continue

        if route == "ACTIVATE" and maps_to in by_canonical:
            # DeepSeek (or other LLM) must be lexicon-grounded or we demote.
            if "deepseek" in method.lower() or method.lower() == "llm":
                if not lexicon_grounded(text, maps_to):
                    demoted_count += 1
                    if ref_k in seen_new:
                        continue
                    seen_new.add(ref_k)
                    new_index += 1
                    rules.append(_new_rule_entry(
                        entry,
                        demoted=True,
                        demote_reason=(
                            f"deepseek ACTIVATE->{maps_to} without lexicon "
                            f"grounding; kept as NEW_RULE"
                        ),
                        new_index=new_index,
                    ))
                    continue

            canon_entry = by_canonical[maps_to]
            if ref_k in seen_refs[maps_to]:
                continue
            enriched = {
                **ref,
                "method": method,
                "is_rule": entry.get("is_rule"),
                "maps_to_conf": entry.get("maps_to_conf"),
            }
            canon_entry["source_refs"].append(enriched)
            seen_refs[maps_to].add(ref_k)
            continue

        if route in ("NEW_RULE", "ACTIVATE", "LLM_FALLBACK"):
            # ACTIVATE to unknown / NONE, or explicit NEW
            if ref_k in seen_new:
                continue
            # also skip if already attached to some canonical with same quote
            if any(ref_k in s for s in seen_refs.values()):
                continue
            seen_new.add(ref_k)
            new_index += 1
            demoted = route == "ACTIVATE" and maps_to not in by_canonical
            reason = None
            if demoted:
                reason = f"ACTIVATE to non-catalog label {maps_to!r}"
                demoted_count += 1
            rules.append(_new_rule_entry(
                entry, demoted=demoted, demote_reason=reason, new_index=new_index,
            ))

    store_stats = store.get("stats") or {}
    stats = {
        "params_from_sheet": store_stats.get("params_from_sheet", 0),
        "param_conflicts": store_stats.get("param_conflicts", 0),
        "rules_total": len(rules),
        "canonical": sum(1 for r in rules if r["origin"] == "canonical"),
        "new_agent": sum(1 for r in rules if r["origin"] == "agent"),
        "new_deepseek": sum(1 for r in rules if r["origin"] == "deepseek_fallback"),
        "enabled": sum(1 for r in rules if r["enabled"]),
        "demoted": demoted_count,
        "rejected": len(rejected),
        "discovery_candidates": (discovered or {}).get("candidates"),
    }

    metrics = store.get("metrics") or {}
    # New rules stay disabled here on purpose. Their conditions are compiled
    # and validated later, by codegen.enrich_new_rules, which is the only
    # place that can tell an executable rule from one the evaluator would
    # refuse -- enabling them before that produced rules that looked active
    # and were UNSUPPORTED at evaluation time.

    return {
        "schema_version": MERGED_SCHEMA_VERSION,
        "run_id": run_id,
        "ruleset_version": store.get("ruleset_version"),
        "policy_id": store.get("policy_id") or "balanced",
        "policy_class": store.get("policy_class") or "balanced",
        "metrics": metrics,
        "generated_at": generated_at,
        "precedence": store.get("precedence", ["NO_PAGAR", "ESCALAR", "PAGAR"]),
        "inputs": {
            "store_schema_version": store.get("store_schema_version"),
            "discovered_folder": (discovered or {}).get("scanned_folder"),
        },
        "rules": rules,
        "rejected": rejected,
        "stats": stats,
    }


def save_merged(merged: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def merge_files(store_path: str,
                discovered_path: Optional[str],
                out_path: str,
                run_id: Optional[str] = None) -> dict:
    store = load_json(store_path)
    discovered = load_json(discovered_path) if discovered_path else None
    merged = build_merged(store, discovered, run_id=run_id)
    save_merged(merged, out_path)
    return merged
