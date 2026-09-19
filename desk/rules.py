"""LEGISLADOR -- change the policy by saying it, never by editing YAML blind.

A rule change is always: parse -> DIFF -> BACKTEST over everything already
decided -> human approves -> new ruleset version -> optional reprocess. The
engine stays deterministic; this is what makes a deterministic engine editable
by someone who does not write YAML.

Recomputation is real, not cosmetic: every RULE_EVALUATED event stores the
values that were compared, so a new parameter can be re-applied to them.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import re
import unicodedata

from desk import engine, state
from desk.ledger import Ledger

BASE = {
    "version": "v3.1-balanced",
    "amount_tolerance_eur": 0.01,
    "authorization_threshold_eur": None,
    "soft_duplicate_verdict": "NEEDS_REVIEW",
    "enforce_payment_terms": True,
    "missing_pedido_exemptions": [],     # [{vendor_id, vendor, max_eur}]
}


def _fold(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()


def current(led: Ledger) -> dict:
    rs = dict(BASE)
    rs["missing_pedido_exemptions"] = []
    for ev in led.by_kind("RULE_APPLIED"):
        for k, v in ev["payload"].get("params", {}).items():
            rs[k] = v
        rs["version"] = ev["payload"]["to_version"]
    return rs


def _next_version(v: str) -> str:
    m = re.match(r"v(\d+)\.(\d+)(.*)", v)
    return f"v{m.group(1)}.{int(m.group(2)) + 1}{m.group(3)}" if m else v + ".1"


# --------------------------------------------------------------------------- #
# parse: natural language -> parameter change
# --------------------------------------------------------------------------- #
def _amount(text: str) -> float | None:
    m = re.search(r"(\d[\d.\s]*(?:,\d+)?)\s*(?:eur|euros|€|k)?", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(" ", "").replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return None
    if re.search(r"\d\s*k\b", text):
        val *= 1000
    return val


def parse(led: Ledger, text: str) -> dict | None:
    """Deterministic intent parse. Returns a proposal or None if not understood."""
    t = _fold(text)
    rs = current(led)

    if re.search(r"(segunda firma|autoriza|escala|revis).{0,40}(por encima|superior|mas de|>)", t) \
       or re.search(r"(por encima|superior|mas de|>).{0,40}(segunda firma|autoriza|escala|revis)", t):
        amt = _amount(t)
        if amt:
            return {"params": {"authorization_threshold_eur": amt},
                    "title": f"Escalar toda factura por encima de {amt:,.0f} EUR",
                    "rationale": "Nueva regla de autorizacion: requiere segunda firma.",
                    "new_rule": True}

    if "toleran" in t:
        amt = _amount(t)
        if amt is not None:
            return {"params": {"amount_tolerance_eur": amt},
                    "title": f"Tolerancia de importe a {amt:.2f} EUR",
                    "rationale": "Modifica un parametro existente de la regla AMOUNT.",
                    "new_rule": False}

    if "duplicad" in t and ("blando" in t or "soft" in t):
        verdict = "FAIL" if "no pagar" in t or "rechaz" in t else "PASS" if "pagar" in t else None
        if verdict:
            return {"params": {"soft_duplicate_verdict": verdict},
                    "title": f"Duplicados blandos -> {'NO_PAGAR' if verdict == 'FAIL' else 'PAGAR'}",
                    "rationale": "Cambia el veredicto de la coincidencia blanda en DUPLICATES.",
                    "new_rule": False}

    if ("condicion" in t or "plazo" in t or "vencim" in t) and \
       re.search(r"(no|deja|ignora|sin)\b", t):
        return {"params": {"enforce_payment_terms": False},
                "title": "Dejar de escalar por condiciones de pago",
                "rationale": "Desactiva enforce_payment_terms en la regla DATES.",
                "new_rule": False}

    if "pedido" in t and re.search(r"(sin|falta|no trae|no tiene)", t):
        amt = _amount(t)
        vendors = {r["invoice"]["vendor_id"]: r["invoice"]["vendor"]
                   for r in state.invoices(led)}
        hit = next(((vid, name) for vid, name in vendors.items()
                    if _fold(name).split()[0] in t), None)
        if hit and amt:
            ex = list(rs["missing_pedido_exemptions"]) + [
                {"vendor_id": hit[0], "vendor": hit[1], "max_eur": amt}]
            return {"params": {"missing_pedido_exemptions": ex},
                    "title": f"Exencion de pedido para {hit[1]} por debajo de {amt:,.0f} EUR",
                    "rationale": "Anade una excepcion parametrizada a la regla MISSING.",
                    "new_rule": True}
    return None


# --------------------------------------------------------------------------- #
# recompute + backtest
# --------------------------------------------------------------------------- #
def _recompute(row: dict, rs: dict) -> tuple[str, list[dict]]:
    evidence = row.get("evaluation")
    if not evidence:
        raise ValueError("Falta la evidencia congelada: ingiere de nuevo o abre una demo nueva.")
    return engine.evaluate(evidence, rs)


def backtest(led: Ledger, proposal: dict) -> dict:
    rs = {**current(led), **proposal["params"]}
    flips, rows = [], state.invoices(led)
    for row in rows:
        new, _ = _recompute(row, rs)
        if new != row["decision"]:
            flips.append({"file_id": row["file_id"], "vendor": row["invoice"]["vendor"],
                          "total": float(row["invoice"]["total"] or 0), "from": row["decision"],
                          "to": new, "already_paid": row["status"] == "paid"})
    loosened = [f for f in flips if f["to"] == "PAGAR"]
    tightened = [f for f in flips if f["from"] == "PAGAR"]
    risky = [f for f in loosened if f["total"] > 1000] + \
            [f for f in flips if f["already_paid"]]
    return {"evaluated": len(rows), "flips": flips,
            "loosened": len(loosened), "tightened": len(tightened),
            "loosened_eur": round(sum(f["total"] for f in loosened), 2),
            "tightened_eur": round(sum(f["total"] for f in tightened), 2),
            "risky": risky[:5], "params": rs}


def validate_params(params: dict) -> bool:
    if not isinstance(params, dict) or not params or set(params) - (set(BASE) - {"version"}):
        return False
    for key, value in params.items():
        if key == "missing_pedido_exemptions":
            if not isinstance(value, list):
                return False
            for ex in value:
                if not isinstance(ex, dict) or set(ex) != {"vendor_id", "vendor", "max_eur"}:
                    return False
                if not all(isinstance(ex[k], str) and ex[k] for k in ("vendor", "vendor_id")):
                    return False
                if type(ex["max_eur"]) not in (int, float) or not math.isfinite(ex["max_eur"]) or ex["max_eur"] <= 0:
                    return False
        elif key == "enforce_payment_terms":
            if type(value) is not bool:
                return False
        elif key == "soft_duplicate_verdict":
            if value not in ("PASS", "FAIL", "NEEDS_REVIEW"):
                return False
        elif key == "authorization_threshold_eur" and value is None:
            continue
        elif type(value) not in (int, float) or not math.isfinite(value) or value < 0 or key == "authorization_threshold_eur" and value == 0:
            return False
    return True


def _basis(led: Ledger) -> str:
    values = [(r["file_id"], r["history"][-1]["seq"], (r["payment"] or {}).get("seq"))
              for r in state.invoices(led)]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _yaml(policy: dict) -> list[str]:
    return [f"{key}: {json.dumps(value, ensure_ascii=False, allow_nan=False)}\n"
            for key, value in sorted(policy.items())]


def propose(led: Ledger, text: str, *, thread_id=None) -> dict | None:
    prop = parse(led, text)
    if prop is None:
        from desk.llm_contract import parse_rule
        params = parse_rule(led, text, thread_id=thread_id)
        if params:
            prop = {"params": params, "title": "Propuesta de norma: " + ", ".join(params),
                    "rationale": "Interpretacion asistida; pendiente de aprobacion humana.",
                    "new_rule": "authorization_threshold_eur" in params}
    if not prop or not validate_params(prop["params"]):
        return None
    with led.transaction():
        bt = backtest(led, prop)
        rs = current(led)
        target = {**rs, **prop["params"], "version": _next_version(rs["version"])}
        diff = "".join(difflib.unified_diff(_yaml(rs), _yaml(target),
                                           fromfile=rs["version"] + ".yaml",
                                           tofile=target["version"] + ".yaml"))
        prop.update(from_version=rs["version"], to_version=target["version"],
                    backtest=bt, source_text=text, diff=diff, basis=_basis(led))
        prop["seq"] = led.record("RULE_PROPOSED", actor="agent:legislador",
                                 thread_id=thread_id, **copy.deepcopy(prop))
    return prop


def apply(led: Ledger, proposal: dict, *, actor="human:alberto", reprocess=True,
          thread_id=None) -> dict:
    with led.transaction():
        event = next((e for e in led.by_kind("RULE_PROPOSED")
                      if e["seq"] == proposal.get("seq")), None)
        if not event:
            raise ValueError("La propuesta no consta en el ledger. Solicita diff y backtest primero.")
        saved = event["payload"]
        for key in ("params", "from_version", "to_version", "title"):
            if proposal.get(key) != saved.get(key):
                raise ValueError("La propuesta ha cambiado; solicita un nuevo diff y backtest.")
        rs = current(led)
        if saved["from_version"] != rs["version"] or saved["basis"] != _basis(led):
            raise ValueError("La propuesta esta desactualizada; solicita un nuevo backtest.")
        next_rs = {**rs, **saved["params"], "version": saved["to_version"]}
        rows = state.invoices(led)
        evaluated = [(row, *_recompute(row, next_rs)) for row in rows] if reprocess else []
        led.record("RULE_APPLIED", actor=actor, thread_id=thread_id,
                   ruleset_version=saved["to_version"], title=saved["title"],
                   params=saved["params"], from_version=saved["from_version"],
                   to_version=saved["to_version"], source_text=saved.get("source_text"),
                   proposed_seq=event["seq"], diff=saved["diff"], backtest=saved["backtest"])
        changed = []
        for row, new, checks in evaluated:
            for c in checks:
                led.record("RULE_EVALUATED", file_id=row["file_id"], actor="engine",
                           ruleset_version=next_rs["version"], **c)
            led.record("VERDICT", file_id=row["file_id"], actor="engine",
                       ruleset_version=next_rs["version"], decision=new, deterministic=True,
                       reprocessed_from=row["decision"], reason_for_reprocess=saved["title"],
                       driven_by=[c["canonical"] for c in checks if c["effect"] != "PAGAR"],
                       checks=checks, evaluation=row["evaluation"], invoice=row["invoice"],
                       source_sha256=(led.last(row["file_id"], "VERDICT") or {}).get("payload", {}).get("source_sha256"))
            if new != row["decision"]:
                changed.append({"file_id": row["file_id"], "from": row["decision"], "to": new})
    return {"version": saved["to_version"], "changed": changed, "reprocessed": len(evaluated)}
