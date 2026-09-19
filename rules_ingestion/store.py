"""store.py · merge predefined + classified-master into one traced ruleset.

The output (rules.store.json) is the frozen artifact the deterministic decision
layer consumes. Every rule records WHY it is there (plan item 6 · TRACE):
  source, ruleset_version, matched_from (sheet/cell/text), is_rule_conf,
  maps_to_conf, method, on_fail, enabled.

Merge model:
  * Start from the predefined CATALOG (our 6 canonical rules).
  * Each norm line JEV routed to ACTIVATE attaches master provenance to its
    canonical rule (a rule can be backed by several norm sentences, e.g. AMOUNT).
  * enabled + params come from the norm PROFILE (rules_v3.yaml).
  * Lines routed NEW_RULE / NOT_A_RULE / LLM-degraded land in `flags`
    (nothing is silently dropped — that is the v4 Saturday hook).
"""
from __future__ import annotations

import inspect
import json
from typing import Dict, List, Optional, Tuple

from . import __version__
from .catalog import CATALOG
from .classify import ClassifyResult
from .loader import LoadResult

STORE_SCHEMA_VERSION = "1.1"   # 1.1: each rule now carries its executable code


def _rule_logic(canonical: str) -> Optional[dict]:
    """The executable Python condition that implements this rule, as source.

    Embeds the actual `if`-code from rules_ingestion.checks so rules.store.json is
    self-describing: the text of the rule AND the code that evaluates it.
    """
    try:
        from .checks import CHECKS
        fn = CHECKS.get(canonical)
        if fn is None:
            return None
        return {
            "language": "python",
            "function": fn.__name__,
            "module": "rules_ingestion.checks",
            "signature": "check(inv, master, params) -> (verdict, reason)",
            "source": inspect.getsource(fn),
        }
    except Exception:
        return None   # code not available -> text-only rule (still valid)


def build_ruleset(load: LoadResult,
                  classified: List[Tuple[dict, ClassifyResult]],
                  profile: dict,
                  generated_at: str = "") -> dict:
    version = profile.get("ruleset_version", load.ruleset_version)
    profile_rules: Dict[str, dict] = profile.get("rules", {})

    # canonical key -> list of master provenance records (ACTIVATE hits)
    activations: Dict[str, List[dict]] = {}
    flags: List[dict] = []
    for line, res in classified:
        if res.route == "ACTIVATE":
            activations.setdefault(res.maps_to, []).append({
                "sheet": load.schema.get("norma", {}).get("sheet", "Norma_Pagos_v3"),
                "cell": line.get("cell"),
                "row": line.get("row"),
                "text": res.text,
                "is_rule_conf": res.is_rule,
                "maps_to_conf": res.maps_to_conf,
                "method": res.method,
            })
        else:
            flags.append({
                "cell": line.get("cell"),
                "text": res.text,
                "route": res.route,
                "is_rule": res.is_rule,
                "maps_to": res.maps_to,
                "maps_to_conf": res.maps_to_conf,
                "method": res.method,
                "reason": res.reason,
            })

    from .policy_metrics import fingerprint
    from .rule_params import from_sheet

    metrics = fingerprint(profile)
    # When the policy allows it, a norm line sets the parameters of the rule it
    # activates. Without this the sheet only ever supplied provenance: the
    # thresholds stayed at whatever the profile said, whatever the norm said.
    sheet_authors_params = bool(metrics.get("sheet_authored_params", False))

    rules: List[dict] = []
    sheet_param_count = 0
    sheet_conflict_count = 0
    for key, canon in CATALOG.items():
        prof = profile_rules.get(key, {})
        enabled = prof.get("enabled", canon.default_enabled)
        params = dict(prof.get("params", {}))
        matched = activations.get(key, [])
        source = "master+predefined" if matched else "predefined"
        on_fail = prof.get("on_fail", canon.on_fail)
        settings, conflicts = (from_sheet(key, matched)
                               if sheet_authors_params else ([], []))
        params.update({setting["param"]: setting["value"] for setting in settings})
        sheet_param_count += len(settings)
        sheet_conflict_count += len(conflicts)
        # A rule the profile leaves off but the sheet states -- the written
        # authorization threshold being the case that matters -- is switched on
        # by the sheet, and the trace records that it was.
        enabled_by_sheet = bool(settings) and not enabled
        if enabled_by_sheet:
            enabled = True
        rules.append({
            "rule_id": canon.rule_id,
            "canonical": key,
            "title": canon.title,
            "description": canon.description,
            "source": source,
            "ruleset_version": version,
            "enabled": enabled,
            "on_fail": on_fail,
            "soft_verdict": canon.soft_verdict,
            "input_fields": list(canon.input_fields),
            "params": params,
            "logic": _rule_logic(key),   # <-- the Python if-code for this rule
            "trace": {
                "matched_from": matched,
                "params_from_sheet": settings,
                "param_conflicts": conflicts,
                "interpretations": list(dict.fromkeys(
                    setting["note"] for setting in settings
                    if setting.get("note"))),
                "enabled_by_sheet": enabled_by_sheet,
                "backed_by_master": bool(matched),
                "is_rule_conf": (round(sum(m["is_rule_conf"] for m in matched) / len(matched), 4)
                                 if matched else None),
                "maps_to_conf": (round(sum(m["maps_to_conf"] for m in matched) / len(matched), 4)
                                 if matched else None),
                "classifier": "jev" if matched else "predefined-only",
                "notes": canon.notes,
            },
        })

    return {
        "store_schema_version": STORE_SCHEMA_VERSION,
        "ruleset_version": version,
        "policy_id": profile.get("policy_id") or profile.get("policy_class") or "balanced",
        "policy_class": profile.get("policy_class") or profile.get("policy_id") or "balanced",
        "metrics": metrics,
        "generated_at": generated_at,
        "generated_with": {"rules_ingestion": __version__},
        "precedence": profile.get("precedence", ["NO_PAGAR", "ESCALAR", "PAGAR"]),
        "verdict_semantics": profile.get("verdict_semantics", {}),
        "rules": rules,
        "flags": flags,
        "master_data": {
            "proveedores": len(load.lookups.get("proveedores", {})),
            "pedidos": len(load.lookups.get("pedidos", {})),
        },
        "schema": load.schema,
        "warnings": load.warnings,
        "stats": {
            "rules_total": len(rules),
            "rules_enabled": sum(1 for r in rules if r["enabled"]),
            "params_from_sheet": sheet_param_count,
            "param_conflicts": sheet_conflict_count,
            "rules_backed_by_master": sum(1 for r in rules if r["trace"]["backed_by_master"]),
            "norma_lines": len(classified),
            "activations": sum(len(v) for v in activations.values()),
            "flags": len(flags),
        },
    }


def save(ruleset: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ruleset, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def load_store(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
