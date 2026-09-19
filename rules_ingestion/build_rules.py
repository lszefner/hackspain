#!/usr/bin/env python3
"""build_rules.py · orchestrate load -> classify -> single outcome.

    python3 -m rules_ingestion.build_rules              # norm -> outcome/<ver>/rules.json
    python3 -m rules_ingestion.build_rules --scan DIR   # discovery folded into same rules.json
    python3 -m rules_ingestion.build_rules --no-llm

One public artifact per version:

    rules_ingestion/outcome/<ruleset_version>/rules.json

Optional cache (gitignored, for re-merge without re-calling JEV):

    .../rules.discovered.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from typing import Optional

from . import loader, store
from .classify import classify_lines

_HERE = os.path.dirname(os.path.abspath(__file__))
_OUTCOME_ROOT = os.path.join(_HERE, "outcome")
_PROFILES_DIR = os.path.join(_HERE, "profiles")
_RULES_NAME = "rules.json"
_DISCOVERED_NAME = "rules.discovered.json"
_DISCOVERED_GEN_NAME = "rules.discovered.gen.json"
_DEFAULT_POLICY = "balanced"


def _version_from_profile(profile_path: str) -> str:
    profile = loader.load_config(profile_path)
    return str(profile.get("ruleset_version") or "v3")


def _policy_id_from_profile(profile_path: str) -> str:
    profile = loader.load_config(profile_path)
    return str(profile.get("policy_id") or profile.get("policy_class") or _DEFAULT_POLICY)


def resolve_profile_path(policy: Optional[str] = None,
                         profile: Optional[str] = None) -> str:
    """Resolve --policy name or --profile path to a YAML file."""
    if profile:
        return profile
    name = policy or _DEFAULT_POLICY
    # Allow legacy rules_v3.yaml as alias of balanced.
    if name in ("v3", "rules_v3"):
        name = "balanced"
    path = os.path.join(_PROFILES_DIR, f"{name}.yaml")
    if os.path.isfile(path):
        return path
    legacy = os.path.join(_HERE, "rules_v3.yaml")
    if name == "balanced" and os.path.isfile(legacy):
        return legacy
    raise FileNotFoundError(
        f"unknown policy {name!r}; expected file {path} "
        f"(available: {', '.join(_list_policy_names())})"
    )


def _list_policy_names() -> list:
    from .policy_metrics import list_policies
    return list_policies(_PROFILES_DIR)


def outcome_dir(version: str, policy: str = _DEFAULT_POLICY) -> str:
    """outcome/<version>/<policy>/ — one folder per ruleset × posture."""
    return os.path.join(_OUTCOME_ROOT, version, policy)


def rules_path(version: str, policy: str = _DEFAULT_POLICY) -> str:
    return os.path.join(outcome_dir(version, policy), _RULES_NAME)


def _write_outcome(say, version: str, store_doc: dict,
                   discovered: Optional[dict] = None,
                   out_path: Optional[str] = None,
                   use_llm: bool = True,
                   gen_python: bool = False,
                   policy: str = _DEFAULT_POLICY) -> dict:
    """Always emit the single schema-2.0 outcome file.

    NEW / demoted rules are compiled to structured `condition` (heuristic always;
    DeepSeek when use_llm). Optional python check when gen_python.
    """
    from . import merge as merge_mod
    from .codegen import enrich_new_rules

    out_path = out_path or rules_path(version, policy)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    merged = merge_mod.build_merged(store_doc, discovered)
    enrich_new_rules(merged, use_llm=use_llm, gen_python=gen_python)
    merge_mod.save_merged(merged, out_path)
    s = merged["stats"]
    say(f"· outcome [{version}/{policy}] -> {os.path.relpath(out_path)}")
    if merged.get("metrics"):
        m = merged["metrics"]
        say(f"  metrics: class={m.get('policy_class')} "
            f"risk={m.get('risk_appetite')} esc_bias={m.get('escalation_bias')} "
            f"auth_thr={m.get('authorization_threshold_eur')}")
    say(f"  canonical={s['canonical']} new_agent={s['new_agent']} "
        f"new_deepseek={s['new_deepseek']} demoted={s['demoted']} "
        f"rejected={s['rejected']} enabled={s['enabled']} "
        f"new_compiled={s.get('new_compiled', 0)}")
    return merged


def _build_store(args, say) -> dict:
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
    s = ruleset["stats"]
    say(f"  store: {s['rules_total']} rules ({s['rules_enabled']} enabled, "
        f"{s['rules_backed_by_master']} backed by master) · "
        f"activations={s['activations']} flags={s['flags']}")
    say(f"  policy: {ruleset.get('policy_id')} · metrics={ruleset.get('metrics')}")
    say(f"  master: {ruleset['master_data']['proveedores']} vendors, "
        f"{ruleset['master_data']['pedidos']} purchase orders")
    if in_tok or out_tok:
        say(f"  JEV usage: {in_tok} in / {out_tok} out tokens "
            f"over {len(load.norma_lines)} line(s)")
    return ruleset


def _discovery_dir(version: str) -> str:
    """Shared discovery cache per ruleset version (posture-independent)."""
    return os.path.join(_OUTCOME_ROOT, version)


def _load_discovery_cache(version: str) -> Optional[dict]:
    odir = _discovery_dir(version)
    for name in (_DISCOVERED_GEN_NAME, _DISCOVERED_NAME):
        path = os.path.join(odir, name)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    return None


def _discovered_entry(line: dict, res, gen_code: bool = False) -> dict:
    entry = {**{k: line[k] for k in ("file", "sheet", "cell", "text")}, **res.to_dict()}
    if res.maps_to and res.maps_to != "NONE" and res.route == "ACTIVATE":
        entry["logic"] = store._rule_logic(res.maps_to)
    elif res.route in ("NEW_RULE", "LLM_FALLBACK") or (
            res.route == "ACTIVATE" and res.maps_to == "NONE"):
        # Always attach at least a heuristic structured condition; optional python.
        from .codegen import compile_new_rule
        compiled = compile_new_rule(res.text, use_llm=gen_code, gen_python=gen_code)
        entry["logic"] = compiled.get("logic") or {
            "language": "python",
            "status": "structured_only",
        }
        entry["compiled_condition"] = compiled["condition"]
        entry["compiled_params"] = compiled.get("params") or {}
        entry["compiled_on_fail"] = compiled.get("on_fail")
    else:
        entry["logic"] = {
            "language": "python",
            "status": "no_code",
            "note": "not a rule / no compile",
        }
    return entry


def _scan(args, say, version: str, policy: str) -> int:
    """Discovery over a folder; fold into the single outcome rules.json."""
    candidates = loader.scan_candidates(args.scan)
    say(f"· scanned '{args.scan}': {len(candidates)} sentence-like candidate(s)")
    tier = "lexical (offline)" if args.no_jev else "JEV System One"
    say(f"· discovering with {tier} · LLM {'OFF' if args.no_llm else 'ON'}")
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

    discovered = {
        "scanned_folder": args.scan,
        "ruleset_version": version,
        "candidates": len(candidates),
        "discovered_canonical": [_discovered_entry(l, r, args.gen_code) for l, r in activate],
        "discovered_new_rules": [_discovered_entry(l, r, args.gen_code) for l, r in new_rule],
        "all": [_discovered_entry(l, r, args.gen_code) for l, r in classified],
    }

    # Optional cache (shared across policies for this version).
    odir = _discovery_dir(version)
    os.makedirs(odir, exist_ok=True)
    cache_name = _DISCOVERED_GEN_NAME if args.gen_code else _DISCOVERED_NAME
    cache_path = os.path.join(odir, cache_name)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(discovered, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    say(f"· discovery cache -> {os.path.relpath(cache_path)} "
        f"({len(activate)} match / {len(new_rule)} new)")
    if in_tok or out_tok:
        say(f"  JEV usage: {in_tok} in / {out_tok} out tokens")

    say("· refreshing single outcome with discovery folded in")
    store_doc = _build_store(args, say)
    _write_outcome(say, version, store_doc, discovered,
                   out_path=args.out or rules_path(version, policy),
                   use_llm=not args.no_llm,
                   gen_python=args.gen_code,
                   policy=policy)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Build Alberto's payment ruleset.")
    p.add_argument("--config", default=os.path.join(_HERE, "sources.yaml"))
    p.add_argument("--policy", default=_DEFAULT_POLICY,
                   help="policy posture: balanced | strict | conservative "
                        f"(default {_DEFAULT_POLICY})")
    p.add_argument("--profile", default=None,
                   help="explicit profile YAML path (overrides --policy)")
    p.add_argument("--out", default=None,
                   help=f"outcome path (default: outcome/<ver>/<policy>/{_RULES_NAME})")
    p.add_argument("--no-jev", action="store_true", help="disable JEV API (offline lexical tier)")
    p.add_argument("--no-llm", action="store_true", help="disable LLM fallback")
    p.add_argument("--scan", metavar="FOLDER",
                   help="DISCOVERY: scan CSV/XLSX and fold into the same rules.json")
    p.add_argument("--gen-code", dest="gen_code", action="store_true",
                   help="with --scan: DeepSeek generates a Python check for each NEW rule")
    p.add_argument("--verbose", action="store_true", help="log every JEV/DeepSeek API call live")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--list-policies", action="store_true",
                   help="print available policy ids and exit")
    args = p.parse_args(argv)

    from . import classify as _classify
    _classify.set_debug(args.verbose)

    def say(*a):
        if not args.quiet:
            print(*a)

    if args.list_policies:
        for name in _list_policy_names():
            print(name)
        return 0

    args.profile = resolve_profile_path(policy=args.policy, profile=args.profile)
    version = _version_from_profile(args.profile)
    policy = _policy_id_from_profile(args.profile)
    say(f"· ruleset version: {version} · policy: {policy}")
    say(f"· profile: {os.path.relpath(args.profile)}")

    if args.scan:
        return _scan(args, say, version, policy)

    store_doc = _build_store(args, say)
    discovered = _load_discovery_cache(version)
    if discovered:
        say(f"· folding discovery cache ({discovered.get('candidates')} candidates)")
    _write_outcome(say, version, store_doc, discovered,
                   out_path=args.out or rules_path(version, policy),
                   use_llm=not args.no_llm,
                   gen_python=False,
                   policy=policy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
