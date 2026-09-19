"""policy_metrics.py · fingerprint a policy profile for UI / comparison later.

Pure functions. No I/O. The decision engine and a future settings UI can call
`fingerprint(profile)` or `diff_metrics(a, b)` without running classifications.

Metric keys are the contract — keep them stable even if param names evolve.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Stable metric catalogue (UI can render labels from this).
METRIC_DEFS: Dict[str, dict] = {
    "policy_class": {
        "type": "enum",
        "values": ["balanced", "strict", "conservative"],
        "label": "Policy class",
        "description": "Named posture bucket for the UI selector.",
    },
    "risk_appetite": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "label": "Risk appetite",
        "description": "Higher = more willing to auto-decide (PAGAR or hard NO_PAGAR).",
    },
    "escalation_bias": {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "label": "Escalation bias",
        "description": "Higher = prefer ESCALAR over automatic PAGAR / NO_PAGAR.",
    },
    "authorization_threshold_eur": {
        "type": "float_or_null",
        "label": "Authorization threshold (EUR)",
        "description": "Amount above which human approval is required; null = off.",
    },
    "iban_mismatch_on_fail": {
        "type": "enum",
        "values": ["ESCALAR", "NO_PAGAR"],
        "label": "IBAN mismatch severity",
        "description": "What an IBAN≠master signal becomes.",
    },
    "soft_duplicate_verdict": {
        "type": "enum",
        "values": ["NEEDS_REVIEW", "FAIL"],
        "label": "Soft-duplicate verdict",
        "description": "amount+date collision → review vs hard fail.",
    },
    "amount_tolerance_eur": {
        "type": "float",
        "label": "Amount tolerance (EUR)",
        "description": "Allowed delta for base+IVA / PO match.",
    },
    "require_erp_pending": {
        "type": "bool",
        "label": "Require ERP PENDIENTE",
        "description": "Block pay if ERP state is not PENDIENTE.",
    },
    "enforce_payment_terms": {
        "type": "bool",
        "label": "Enforce payment terms",
        "description": "Check vendor Condiciones window.",
    },
    "check_nif_control_digit": {
        "type": "bool",
        "label": "NIF control digit",
        "description": "Validate Spanish tax-ID checksum.",
    },
    "enable_authorization": {
        "type": "bool",
        "label": "Authorization rule on",
        "description": "Whether amount-threshold approval is active.",
    },
    "enable_new_rules_default": {
        "type": "bool",
        "label": "Auto-enable NEW rules",
        "description": "Discovered NEW rules start enabled under this policy.",
    },
}


def _auth_threshold(profile: dict) -> Optional[float]:
    auth = (profile.get("rules") or {}).get("AUTHORIZATION") or {}
    if not auth.get("enabled", False):
        return None
    params = auth.get("params") or {}
    thr = params.get("escalate_above_eur")
    return float(thr) if thr is not None else None


def derive_metrics(profile: dict) -> Dict[str, Any]:
    """Derive metrics from rule params when `metrics:` block is missing/partial."""
    rules = profile.get("rules") or {}
    vendor = (rules.get("VENDOR") or {})
    vparams = vendor.get("params") or {}
    dups = (rules.get("DUPLICATES") or {}).get("params") or {}
    amount = (rules.get("AMOUNT") or {}).get("params") or {}
    dates = (rules.get("DATES") or {}).get("params") or {}
    auth = rules.get("AUTHORIZATION") or {}

    derived = {
        "policy_class": profile.get("policy_class") or profile.get("policy_id") or "balanced",
        "authorization_threshold_eur": _auth_threshold(profile),
        "iban_mismatch_on_fail": vendor.get("on_fail") or "ESCALAR",
        "soft_duplicate_verdict": dups.get("soft_duplicate_verdict") or "NEEDS_REVIEW",
        "amount_tolerance_eur": float(amount.get("tolerance_eur", 0.01)),
        "require_erp_pending": bool(dups.get("require_erp_pending", True)),
        "enforce_payment_terms": bool(dates.get("enforce_payment_terms", True)),
        "check_nif_control_digit": bool(vparams.get("check_nif_control_digit", True)),
        "enable_authorization": bool(auth.get("enabled", False)),
        "enable_new_rules_default": bool(
            (profile.get("metrics") or {}).get("enable_new_rules_default", False)
        ),
    }
    # Optional explicit overrides already on the profile.
    explicit = profile.get("metrics") or {}
    out = {**derived, **{k: explicit[k] for k in explicit}}
    # Ensure appetite/bias always present (defaults by class).
    defaults = {
        "balanced": (0.50, 0.55),
        "strict": (0.85, 0.25),
        "conservative": (0.25, 0.85),
    }
    cls = str(out.get("policy_class") or "balanced")
    risk, esc = defaults.get(cls, (0.50, 0.55))
    out.setdefault("risk_appetite", risk)
    out.setdefault("escalation_bias", esc)
    return out


def fingerprint(profile: dict) -> Dict[str, Any]:
    """Public API: stable metrics dict to embed in rules.json / show in UI."""
    m = derive_metrics(profile)
    # Only emit declared metric keys (forward-compatible).
    return {k: m.get(k) for k in METRIC_DEFS}


def diff_metrics(a: dict, b: dict) -> List[dict]:
    """List metrics that differ between two fingerprints (for impact UI later)."""
    fa, fb = fingerprint(a) if "rules" in a else a, fingerprint(b) if "rules" in b else b
    # allow passing raw fingerprints too
    if "policy_class" in a and "rules" not in a:
        fa = {k: a.get(k) for k in METRIC_DEFS}
    if "policy_class" in b and "rules" not in b:
        fb = {k: b.get(k) for k in METRIC_DEFS}
    diffs = []
    for key in METRIC_DEFS:
        if fa.get(key) != fb.get(key):
            diffs.append({
                "metric": key,
                "label": METRIC_DEFS[key]["label"],
                "from": fa.get(key),
                "to": fb.get(key),
            })
    return diffs


def list_policies(profiles_dir: str) -> List[str]:
    """Return policy_id stems available under profiles/ (excluding README)."""
    import os
    if not os.path.isdir(profiles_dir):
        return []
    out = []
    for name in sorted(os.listdir(profiles_dir)):
        if name.endswith(".yaml") and not name.startswith("_"):
            out.append(name[: -len(".yaml")])
    return out
