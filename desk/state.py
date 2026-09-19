"""Current state as a FOLD over the ledger. Never a mutated row.

Every projection here is derivable from events alone, which is what lets the
system answer "it was PAGAR at 09:12 under v3.1 and ESCALAR at 18:40 under v4"
instead of only ever knowing the latest value.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from desk.ledger import Ledger

TERMINAL = {"PAGADA": "paid", "RECHAZADA": "blocked"}


def _effect(check: dict) -> str:
    return check.get("effect", "PAGAR" if check["verdict"] == "PASS" else "ESCALAR")


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _fold_one(events: list[dict]) -> dict | None:
    inv, checks, decision, ruleset = None, [], None, None
    evaluation = None
    cost = ms = 0.0
    cost_unknown = 0
    investigation = approval = payment = None
    emails: list[dict] = []
    retries = 0
    fallback = False
    provider_failed = False
    history: list[dict] = []

    for ev in events:
        p, kind = ev["payload"], ev["kind"]
        if not (ev["file_id"] is None and p.get("file_ids")):
            cost += ev["cost_usd"] or 0
            ms += ev["ms"] or 0
            if p.get("cost_known") is False:
                cost_unknown += 1
        if kind == "PROVIDER_FAILED":
            provider_failed = True
        elif kind == "EXTRACTED" and provider_failed:
            fallback = True
        elif kind == "ERP_RECONCILED":
            retries += p.get("retries") or 0
        elif kind == "RULE_EVALUATED":
            checks = [c for c in checks if c["canonical"] != p.get("canonical")] + [p]
        elif kind == "VERDICT":
            inv = p.get("invoice") or inv
            decision = p.get("decision")
            ruleset = ev["ruleset_version"]
            evaluation = p.get("evaluation") or evaluation
            if p.get("checks") is not None:
                checks = p["checks"]
            # a new verdict invalidates whatever the investigator concluded
            investigation = None
            history.append({"seq": ev["seq"], "ts": ev["ts"], "decision": decision,
                            "ruleset_version": ruleset})
        elif kind == "INVESTIGATED":
            investigation = {**p, "seq": ev["seq"], "ts": ev["ts"]}
        elif kind in ("HUMAN_APPROVED", "HUMAN_REJECTED"):
            approval = {"outcome": kind, "actor": ev["actor"], "ts": ev["ts"], **p}
        elif kind == "PAYMENT_REGISTERED":
            payment = {**p, "ts": ev["ts"], "seq": ev["seq"]}
        elif kind in ("EMAIL_QUEUED", "EMAIL_SENT", "EMAIL_CANCELLED",
                      "EMAIL_FAILED", "EMAIL_UNCERTAIN", "EMAIL_DISPATCH_STARTED"):
            emails.append({"kind": kind, "ts": ev["ts"], **p})

    if inv is None:
        return None

    if payment:
        status = "paid"
    elif approval and approval["outcome"] == "HUMAN_REJECTED":
        status = "rejected"
    elif decision == "NO_PAGAR":
        status = "blocked"
    elif decision == "ESCALAR":
        status = "escalated"
    else:
        status = "queued"

    return {
        "file_id": inv["file_id"], "invoice": inv, "decision": decision,
        "ruleset_version": ruleset, "checks": checks, "status": status,
        "evaluation": evaluation,
        "blocking": [c for c in checks if _effect(c) != "PAGAR"],
        "investigation": investigation, "approval": approval, "payment": payment,
        "emails": emails, "cost_usd": round(cost, 5), "cost_unknown": cost_unknown,
        "ms": int(ms),
        "erp_retries": retries, "used_fallback": fallback,
        "history": history, "events": len(events),
    }


def invoices(ledger: Ledger) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for ev in ledger.all_events():
        if ev["file_id"]:
            grouped[ev["file_id"]].append(ev)
        else:
            for fid in ev["payload"].get("file_ids") or []:
                grouped[fid].append(ev)
    out = [r for r in (_fold_one(evs) for evs in grouped.values()) if r]
    out.sort(key=lambda r: r["file_id"])
    return out


def get(ledger: Ledger, file_id: str) -> dict | None:
    return _fold_one(ledger.for_file(file_id))


# --------------------------------------------------------------------------- #
# clustering: the reason 40 escalations are not 40 decisions
# --------------------------------------------------------------------------- #
CLUSTER_LABEL = {
    "DUPLICATES": "posible duplicado",
    "VENDOR": "IBAN no coincide con el maestro",
    "MISSING": "falta el numero de pedido",
    "AMOUNT": "el importe no cuadra con el pedido",
    "DATES": "fuera de las condiciones de pago",
}


def clusters(rows: list[dict]) -> list[dict]:
    """Group open escalations by (blocking rule, vendor). Sameness compresses."""
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row["status"] != "escalated":
            continue
        rule = (row["blocking"][0]["canonical"] if row["blocking"] else "OTHER")
        buckets[(rule, row["invoice"]["vendor_id"])].append(row)

    out = []
    for (rule, vendor_id), items in buckets.items():
        items.sort(key=lambda r: r["file_id"])
        total = sum(_num(i["invoice"]["total"]) for i in items)
        recs = {i["investigation"].get("recommendation") for i in items
                if i["investigation"]}
        out.append({
            "id": f"{rule}:{vendor_id}",
            "rule": rule,
            "label": CLUSTER_LABEL.get(rule, rule),
            "vendor": items[0]["invoice"]["vendor"],
            "vendor_id": vendor_id,
            "vendor_email": items[0]["invoice"].get("vendor_email"),
            "count": len(items),
            "total": round(total, 2),
            "file_ids": [i["file_id"] for i in items],
            "items": items,
            "resolved": sum(1 for i in items if i["investigation"]),
            "recommendation": (recs.pop() if len(recs) == 1
                               and all(i["investigation"] for i in items) else None),
        })
    out.sort(key=lambda c: (-c["count"], -c["total"]))
    return out


def _ruleset_version(ledger: Ledger, rows: list[dict]) -> str:
    """The version in force now: the last RULE_APPLIED, else whatever decided."""
    applied = ledger.by_kind("RULE_APPLIED")
    if applied:
        return applied[-1]["payload"].get("to_version", "-")
    return next((r["ruleset_version"] for r in rows if r["ruleset_version"]), "-")


def summary(ledger: Ledger, rows: list[dict] | None = None) -> dict:
    rows = rows if rows is not None else invoices(ledger)
    by_status = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1

    dup_blocked = [r for r in rows
                   if r["status"] == "blocked"
                   and any(c["canonical"] == "DUPLICATES" for c in r["blocking"])]
    saved = round(sum(_num(r["invoice"]["total"]) for r in dup_blocked), 2)
    queued = [r for r in rows if r["status"] == "queued"]
    open_esc = [r for r in rows if r["status"] == "escalated"]
    auto_resolved = [r for r in open_esc if r["investigation"]]

    events = ledger.all_events()
    today = datetime.now(UTC).date().isoformat()
    cost_all = sum(e["cost_usd"] or 0 for e in events)
    cost_today = sum(e["cost_usd"] or 0 for e in events
                     if (e["ts"] or "")[:10] == today)
    cost_unknown = sum(1 for e in events if e["payload"].get("cost_known") is False)
    erp_retries = (
        sum(len(e["payload"].get("retries", [])) for e in ledger.by_kind("ERP_SNAPSHOT"))
        + sum(r["erp_retries"] for r in rows
              if not any(e["payload"].get("shared_snapshot")
                         for e in ledger.for_file(r["file_id"])
                         if e["kind"] == "ERP_RECONCILED"))
    )

    return {
        "total": len(rows),
        "paid": by_status["paid"],
        "queued": len(queued),
        "queued_total": round(sum(_num(r["invoice"]["total"]) for r in queued), 2),
        "escalated": len(open_esc),
        "blocked": by_status["blocked"],
        "rejected": by_status["rejected"],
        "clusters": len(clusters(rows)),
        "auto_resolved": len(auto_resolved),
        "duplicates_blocked": len(dup_blocked),
        "duplicates_saved": saved,
        "cost_usd": round(cost_all, 4),
        "cost_today_usd": round(cost_today, 4),
        "cost_unknown": cost_unknown,
        "cost_per_invoice": round(cost_all / max(len(rows), 1), 5),
        "erp_retries": erp_retries,
        "fallbacks": sum(1 for r in rows if r["used_fallback"]),
        "ruleset_version": _ruleset_version(ledger, rows),
    }
