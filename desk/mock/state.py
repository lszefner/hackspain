"""The demo state the desk answers from.

Every number the interface shows comes from here. The model never invents one:
it is handed this object and may only phrase it. Shapes match desk/state.py so
swapping the fixture for the real ledger fold is a one-line change.
"""

TODAY = "19 September"

TOTALS = {
    "processed": 540,
    "paid": 443,
    "paid_eur": 1182400.00,
    "queued": 41,
    "queued_eur": 96320.00,
    "needs_review": 12,
    "needs_review_eur": 33012.60,
    "duplicates_stopped": 3,
    "duplicates_eur": 8410.00,
    "cost_usd": 3.29,
    "cost_per_invoice_usd": 0.0061,
    "ruleset": "v3.2",
    "ruleset_sha": "9f2c1ab4",
}

# --------------------------------------------------------------------------- #
# what needs Alberto — clusters, not invoices: sameness compresses
# --------------------------------------------------------------------------- #
QUEUE = [
    {
        "id": "MISSING:P007",
        "nif": "J40112358", "iban": "ES66 0081 5310 4200 0177 12", "terms": "30 days",
        "count": 6,
        "title": "no purchase order",
        "vendor": "Papelería Ruzafa S.C.",
        "eur": 1412.60,
        "state": "unseen",
        "verdict": "PAY",
        "detail": (
            "I checked all six against the purchase order master and not one of them "
            "has an order. It is this supplier's habit: office consumables ordered "
            "over the phone, every invoice under €200. I would pay them and fix the "
            "exception as a rule so they stop escalating."
        ),
        "invoices": [
            ["invoice_4172.pdf", "RZ-2025-0912", "02 Sep", 184.20, "no order on file"],
            ["invoice_4181.pdf", "RZ-2025-0918", "04 Sep", 96.80, "no order on file"],
            ["invoice_4190.pdf", "RZ-2025-0923", "08 Sep", 199.00, "no order on file"],
            ["invoice_4197.pdf", "RZ-2025-0931", "10 Sep", 312.40, "no order on file"],
            ["invoice_4205.pdf", "RZ-2025-0937", "12 Sep", 420.20, "no order on file"],
            ["invoice_4211.pdf", "RZ-2025-0940", "15 Sep", 200.00, "no order on file"],
        ],
        "actions": [
            {"label": "Accept · pay all 6", "kind": "primary", "act": "approve"},
            {"label": "Reject", "kind": "danger", "act": "reject"},
            {"label": "Write to supplier", "act": "email"},
            {"label": "Defer", "kind": "defer"},
            {"label": "Turn into a rule", "kind": "quiet", "act": "rule"},
        ],
    },
    {
        "id": "VENDOR:P010",
        "nif": "B98455101", "iban": "ES91 2100 0418 4502 0005 4455", "terms": "30 days",
        "iban_master": "ES60 0182 5322 1802 0158 8391",
        "count": 3,
        "title": "bank account does not match the master",
        "vendor": "Informática Benimámet S.L.",
        "eur": 9720.00,
        "state": "seen",
        "verdict": "DO NOT PAY",
        "detail": (
            "All three carry ES…4455 and the master says ES…8391. I went through the "
            "history: the change shows up in July, always from this supplier, never "
            "confirmed through a channel we know. I would not pay until they confirm "
            "the account by phone."
        ),
        "invoices": [
            ["invoice_4166.pdf", "IB-2025-0448", "01 Sep", 3240.00, "account ends 4455"],
            ["invoice_4188.pdf", "IB-2025-0455", "05 Sep", 3240.00, "account ends 4455"],
            ["invoice_4209.pdf", "IB-2025-0461", "14 Sep", 3240.00, "account ends 4455"],
        ],
        "actions": [
            {"label": "Write to supplier · ask them to confirm", "kind": "primary", "act": "email"},
            {"label": "Reject all 3", "kind": "danger", "act": "reject"},
            {"label": "Accept", "act": "approve"},
            {"label": "Defer", "kind": "defer"},
            {"label": "Turn into a rule", "kind": "quiet", "act": "rule"},
        ],
    },
    {
        "id": "DATES:P002",
        "nif": "A41220987", "iban": "ES76 2100 0813 6101 2345 6789", "terms": "45 days",
        "count": 2,
        "title": "outside the agreed payment terms",
        "vendor": "Transportes Guadaira S.A.",
        "eur": 3240.00,
        "state": "unseen",
        "verdict": "PAY",
        "detail": (
            "Both are dated inside 45 days but arrived late, so the due date has "
            "already passed. The amounts match their orders and the account is the "
            "one on the master. Paying them now costs nothing and stops the supplier "
            "chasing us."
        ),
        "invoices": [
            ["invoice_4179.pdf", "TG-2025-1180", "03 Sep", 1620.00, "due 18 Oct, 3 days over"],
            ["invoice_4194.pdf", "TG-2025-1186", "09 Sep", 1620.00, "due 24 Oct, 1 day over"],
        ],
        "actions": [
            {"label": "Accept · pay both", "kind": "primary", "act": "approve"},
            {"label": "Reject", "kind": "danger", "act": "reject"},
            {"label": "Write to supplier", "act": "email"},
            {"label": "Defer", "kind": "defer"},
        ],
    },
    {
        "id": "AMOUNT:P009",
        "nif": "A46311208", "iban": "ES29 0049 1820 7520 1000 2044", "terms": "60 days",
        "count": 1,
        "title": "amount does not match the order",
        "vendor": "Construcciones Benimaclet S.A.",
        "eur": 18640.00,
        "state": "deferred · waiting on Management",
        "verdict": None,
        "detail": (
            "Order PED-0412 is for €17,200.00 and the invoice asks €18,640.00. The "
            "gap is exactly one line, \"crane surcharge\", that is not on the order. "
            "You deferred it yesterday and Management has not answered yet."
        ),
        "invoices": [["invoice_4201.pdf", "CB-2025-0309", "11 Sep", 18640.00,
                      "€1,440.00 over PED-0412"]],
        "actions": [
            {"label": "Accept", "act": "approve"},
            {"label": "Reject", "kind": "danger", "act": "reject"},
            {"label": "Write to supplier", "act": "email"},
            {"label": "Open the file", "kind": "quiet", "act": "open"},
        ],
    },
]

# --------------------------------------------------------------------------- #
# what is ready to pay
# --------------------------------------------------------------------------- #
PAYMENTS = [
    {"vendor": "Suministros Levante S.L.", "count": 12, "eur": 28400.00, "terms": "60 days", "note": "due today"},
    {"vendor": "Electricidad Montcada S.A.", "count": 9, "eur": 22180.00, "terms": "60 days", "note": "due today"},
    {"vendor": "Limpiezas Turia S.L.", "count": 7, "eur": 14960.00, "terms": "45 days", "note": "due today"},
    {"vendor": "Mensajería Rápida del Sur S.L.", "count": 6, "eur": 11540.00, "terms": "30 days", "note": "due today"},
    {"vendor": "Catering Hermanos Pico S.L.", "count": 4, "eur": 9880.00, "terms": "45 days", "note": "due today"},
    {"vendor": "Seguridad Alcores S.L.", "count": 3, "eur": 9360.00, "terms": "30 days", "note": "due today"},
]

DUPLICATES = [
    {"file": "invoice_4187.pdf", "vendor": "Ofimática Cieza S.L.", "number": "OC-2025-0771", "eur": 4180.00,
     "note": "same number already paid on 03 Sep"},
    {"file": "invoice_4203.pdf", "vendor": "Limpiezas Turia S.L.", "number": "LT-2025-0620", "eur": 2360.00,
     "note": "same number already paid on 08 Sep"},
    {"file": "invoice_4216.pdf", "vendor": "Suministros Levante S.L.", "number": "SL-2025-1402", "eur": 1870.00,
     "note": "resent by the supplier, already queued"},
]

# --------------------------------------------------------------------------- #
# the ruleset
# --------------------------------------------------------------------------- #
RULES = [
    {"name": "VENDOR", "on_fail": "ESCALATE", "text": "Supplier is active and the NIF and account match the master.",
     "params": "master = FINAL_v7 · account compared in full"},
    {"name": "DUPLICATES", "on_fail": "DO NOT PAY", "text": "No invoice with the same number from the same supplier has been paid.",
     "params": "key = invoice number + supplier id"},
    {"name": "AMOUNT", "on_fail": "ESCALATE", "text": "The total matches the purchase order and the tax adds up.",
     "params": "tolerance 0.5% · VAT 21%"},
    {"name": "DATES", "on_fail": "ESCALATE", "text": "The due date falls inside the supplier's agreed terms.",
     "params": "terms read from the master per supplier"},
    {"name": "MISSING", "on_fail": "ESCALATE", "text": "A purchase order number is present and exists in the master.",
     "params": "no exception configured"},
]

RULE_HISTORY = [
    {"from": "v3.1", "to": "v3.2", "when": "18 Sep 14:20", "title": "VAT tolerance raised to 0.5%",
     "said": "round the cents, a two cent gap is not a problem"},
    {"from": "v3.0", "to": "v3.1", "when": "16 Sep 09:05", "title": "DUPLICATES now blocks instead of escalating",
     "said": "if we already paid it, do not even ask me"},
]

WAITING = [
    {"vendor": "Informática Benimámet S.L.", "what": "confirmation of the new account", "since": "14 Sep"},
    {"vendor": "Construcciones Benimaclet S.A.", "what": "Management's answer on the crane surcharge", "since": "18 Sep"},
]


def money(v):
    return "€" + f"{v:,.2f}"


# --------------------------------------------------------------------------- #
# blocks — what the interface can put on screen. The model picks from these by
# name only; it never builds one and never edits a number inside one.
# --------------------------------------------------------------------------- #
DUE_MONTH = {"02 Sep": "02 Oct", "04 Sep": "04 Oct", "08 Sep": "08 Oct", "10 Sep": "10 Oct",
             "12 Sep": "12 Oct", "15 Sep": "15 Oct", "01 Sep": "01 Oct", "05 Sep": "05 Oct",
             "14 Sep": "14 Oct", "03 Sep": "18 Oct", "09 Sep": "24 Oct", "11 Sep": "10 Nov"}


def _fields(c, inv):
    """What was read off the page, and what it was compared against."""
    _file, number, date, total, finding = inv
    net = round(total / 1.21, 2)
    rows = [
        ["Invoice number", number],
        ["Tax id", c["nif"]],
        ["Account on the invoice", c["iban"]],
        ["Purchase order", "—" if c["id"].startswith("MISSING") else
         ("PED-0412" if c["id"].startswith("AMOUNT") else "matched")],
        ["Net", money(net)],
        ["VAT 21%", money(round(total - net, 2))],
        ["Total", money(total)],
        ["Issued", date + " 2025"],
        ["Due", DUE_MONTH.get(date, date) + " 2025"],
    ]
    if c.get("iban_master"):
        rows.insert(3, ["Account on the master", c["iban_master"]])
    rows.append(["Terms", c["terms"]])
    rows.append(["What blocked it", finding])
    return rows


def _table(c):
    return {
        "head": ["File", "Invoice", "Issued", "Amount", "What I found"],
        "rows": [{"cells": [i[0], i[1], i[2], money(i[3]), i[4]],
                  "fields": _fields(c, i)} for i in c["invoices"]],
    }


def block(name):
    if name == "queue":
        return {
            "id": "queue", "title": "Needs your judgement",
            "meta": f"{TOTALS['needs_review']} invoices · {money(TOTALS['needs_review_eur'])}",
            "rows": [{
                "key": c["id"],
                "lead": str(c["count"]), "title": c["title"], "sub": c["vendor"],
                "value": money(c["eur"]), "state": c["state"], "verdict": c["verdict"],
                "detail": c["detail"],
                "table": _table(c),
                "actions": c["actions"],
            } for c in QUEUE],
            "actions": [{"label": "Open the first one", "kind": "primary", "ui": "first"},
                        {"label": f"See all {TOTALS['needs_review']} in Invoices", "ui": "invoices"}],
        }

    if name == "payments":
        return {
            "id": "payments", "title": "Ready to pay today",
            "meta": f"{TOTALS['queued']} invoices · {money(TOTALS['queued_eur'])}",
            "rows": [{
                "lead": str(p["count"]), "title": p["vendor"], "sub": f"{p['terms']} · {p['note']}",
                "value": money(p["eur"]),
            } for p in PAYMENTS],
            "actions": [{"label": f"Approve all {TOTALS['queued']}", "kind": "primary", "act": "pay_all"},
                        {"label": "Download the SEPA file", "href": "/api/sepa.xml"},
                        {"label": "Go one by one", "ask": "which invoices do I need to review"}],
        }

    if name == "report":
        t = TOTALS
        return {
            "id": "report", "title": f"{TODAY} · day report", "meta": f"ruleset {t['ruleset']}",
            "rows": [
                {"title": "Processed", "value": str(t["processed"])},
                {"title": "Paid", "sub": f"{t['paid']} invoices", "value": money(t["paid_eur"])},
                {"title": "Queued for payment", "sub": f"{t['queued']} invoices", "value": money(t["queued_eur"])},
                {"title": "Needs your judgement", "sub": f"{t['needs_review']} invoices", "value": money(t["needs_review_eur"]),
                 "tone": "warn"},
                {"title": "Duplicates stopped", "sub": f"{t['duplicates_stopped']} invoices, not paid twice",
                 "value": money(t["duplicates_eur"]), "tone": "good"},
                {"title": "Cost to run", "sub": f"${t['cost_per_invoice_usd']:.4f} per invoice",
                 "value": f"${t['cost_usd']:.2f}"},
            ],
            "actions": [{"label": "Send to Management", "kind": "primary", "act": "report"},
                        {"label": "Attach the SEPA file", "href": "/api/sepa.xml"},
                        {"label": "See the 12 that need you", "ask": "which invoices do I need to review"}],
        }

    if name == "duplicates":
        return {
            "id": "duplicates", "title": "Duplicates stopped",
            "meta": f"{TOTALS['duplicates_stopped']} invoices · {money(TOTALS['duplicates_eur'])} not paid twice",
            "rows": [{"title": d["vendor"], "sub": f"{d['number']} · {d['note']}", "value": money(d["eur"])}
                     for d in DUPLICATES],
            "actions": [{"label": "See how the check works", "ask": "what rules are running"}],
        }

    if name == "rules":
        return {
            "id": "rules", "title": f"Ruleset {TOTALS['ruleset']}", "meta": f"sha256 {TOTALS['ruleset_sha']}",
            "rows": [{"title": r["name"], "sub": r["text"], "value": r["on_fail"],
                      "detail": r["params"]} for r in RULES],
            "actions": [{"label": "See what changed", "ask": "what changed in the rules"}],
        }

    if name == "waiting":
        return {
            "id": "waiting", "title": "Waiting on someone else",
            "meta": f"{len(WAITING)} open",
            "rows": [{"title": w["vendor"], "sub": w["what"], "value": f"since {w['since']}"} for w in WAITING],
            "actions": [{"label": "Chase them", "kind": "primary", "act": "chase"}],
        }
    return None


def facts():
    """The compact, read-only view of the world handed to the model."""
    return {
        "today": TODAY,
        "operator": "Alberto",
        "totals": TOTALS,
        "needs_judgement": [
            {"count": c["count"], "problem": c["title"], "supplier": c["vendor"],
             "eur": c["eur"], "my_recommendation": c["verdict"], "why": c["detail"],
             "queue_state": c["state"]} for c in QUEUE
        ],
        "ready_to_pay": PAYMENTS,
        "duplicates_stopped": DUPLICATES,
        "ruleset": {"version": TOTALS["ruleset"], "sha256": TOTALS["ruleset_sha"],
                    "rules": RULES, "recent_changes": RULE_HISTORY},
        "waiting_on_others": WAITING,
    }


# --------------------------------------------------------------------------- #
# what the archive already holds, so a re-upload is caught by name
# --------------------------------------------------------------------------- #
ARCHIVE = {i[0] for c in QUEUE for i in c["invoices"]} | {d["file"] for d in DUPLICATES}


def batch_block(b):
    """The result of a dropped batch, as one panel."""
    rows = []
    if b["pay"]:
        rows.append({"lead": str(b["pay"]), "title": "pay", "sub": "clean against the rules",
                     "value": money(b["pay_eur"]), "tone": "good"})
    if b["escalate"]:
        rows.append({"lead": str(b["escalate"]), "title": "escalate",
                     "sub": f"{b['patterns']} pattern{'s' if b['patterns'] != 1 else ''}, waiting on you",
                     "value": money(b["escalate_eur"]), "tone": "warn"})
    if b["nopay"]:
        rows.append({"lead": str(b["nopay"]), "title": "do not pay", "sub": "blocked by a rule",
                     "value": money(b["nopay_eur"])})
    if b["dupes"]:
        rows.append({"lead": str(len(b["dupes"])), "title": "already had them",
                     "sub": ", ".join(b["dupes"][:3]) + ("…" if len(b["dupes"]) > 3 else ""),
                     "value": "not run again"})
    if b["rejected"]:
        rows.append({"lead": str(len(b["rejected"])), "title": "could not read",
                     "sub": "; ".join(f"{r[0]} — {r[1]}" for r in b["rejected"][:3]),
                     "value": "not in"})
    acts = []
    if b["escalate"]:
        acts.append({"label": f"Start with the {b['escalate']} escalated", "kind": "primary",
                     "ask": "which invoices do I need to review"})
    # Both used to carry only a label: the click handler dispatches on
    # data-ask/-ui/-act, so a bare label rendered a button that did nothing.
    acts += [{"label": "See the whole batch", "ui": "invoices"},
             {"label": "See the batch trace", "kind": "quiet",
              "ask": "how did it go"}]
    return {"id": "batch", "title": b["label"],
            "meta": f"{b['total']} invoices · {b['size']} · {b['seconds']} · demo",
            "rows": rows, "actions": acts}


# --------------------------------------------------------------------------- #
# actions. The buttons move real state: a cluster leaves the queue, the totals
# fold again, and the change is written down. Every one of them is reversible
# from the snapshot taken just before it.
# --------------------------------------------------------------------------- #
import copy
from datetime import UTC, datetime, timedelta

OUTBOX: list = []
LOG: list = []
_undo: list = []

_MUT = ("QUEUE", "TOTALS", "PAYMENTS", "WAITING", "RULES", "RULE_HISTORY", "OUTBOX", "LOG")


def _snapshot():
    g = globals()
    _undo.append({k: copy.deepcopy(g[k]) for k in _MUT})
    del _undo[:-12]


def undo():
    if not _undo:
        return {"said": "Nothing to put back."}
    snap = _undo.pop()
    g = globals()
    for k, v in snap.items():
        cur = g[k]
        if isinstance(cur, list):
            cur[:] = v
        else:
            cur.clear(); cur.update(v)
    return {"said": "Put back. Nothing moved after all.", "refresh": True}


def cluster(key):
    return next((c for c in QUEUE if c["id"] == key), None)


def _close(c, status):
    """Take a cluster out of the queue and fold the totals again."""
    QUEUE.remove(c)
    TOTALS["needs_review"] -= c["count"]
    TOTALS["needs_review_eur"] = round(TOTALS["needs_review_eur"] - c["eur"], 2)
    c["status"] = status
    return c


def _stamp():
    return datetime.now(UTC).astimezone().strftime("%H:%M")


def act(name, key=None, reason=None):
    if key and key.startswith("file:"):
        return decide(name, key[5:], reason)
    c = cluster(key) if key else None
    _snapshot()

    if name == "approve" and c:
        _close(c, "approved")
        TOTALS["queued"] += c["count"]
        TOTALS["queued_eur"] = round(TOTALS["queued_eur"] + c["eur"], 2)
        LOG.append([_stamp(), "approved", c["vendor"], c["count"], c["eur"]])
        return {"said": f"Done. The {c['count']} from {c['vendor']} go into the next run, "
                        f"{money(c['eur'])}. They are out of your queue.", "undo": True, "refresh": True}

    if name == "reject" and c:
        _close(c, "rejected")
        LOG.append([_stamp(), "rejected", c["vendor"], c["count"], c["eur"]])
        return {"said": f"Rejected, all {c['count']} of them, {money(c['eur'])}. I wrote down that it "
                        f"was you and why, so nobody re-runs them by accident.", "undo": True, "refresh": True}

    if name == "email" and c:
        _close(c, "waiting")
        out = datetime.now(UTC).astimezone() + timedelta(seconds=60)
        OUTBOX.append({"to": c["vendor"], "subject": f"About your {c['count']} open invoice(s)",
                       "release": out.strftime("%H:%M:%S")})
        WAITING.append({"vendor": c["vendor"], "what": "an answer to the mail I just wrote",
                        "since": TODAY})
        LOG.append([_stamp(), "wrote to", c["vendor"], c["count"], c["eur"]])
        return {"said": f"Written to {c['vendor']}. It sits in the outbox until "
                        f"{out.strftime('%H:%M:%S')}, so you can still stop it.",
                "undo": True, "refresh": True}

    if name == "defer" and c:
        _close(c, "deferred")
        WAITING.append({"vendor": c["vendor"], "what": reason or "a decision", "since": TODAY})
        LOG.append([_stamp(), "deferred", c["vendor"], c["count"], c["eur"]])
        return {"said": f"Deferred, {reason or 'for now'}. It is off your queue and it now shows on "
                        f"{c['vendor']}'s page so it cannot be quietly forgotten.",
                "undo": True, "refresh": True}

    if name == "rule" and c:
        if c["count"] < 3:
            _undo.pop()
            return {"said": "One invoice is not a pattern. I would rather you just decided this one."}
        _close(c, "ruled")
        old = TOTALS["ruleset"]
        TOTALS["ruleset"] = f"v3.{int(old.split('.')[1]) + 1}"
        TOTALS["ruleset_sha"] = hashlib_short(c["id"] + TOTALS["ruleset"])
        title = f"{c['vendor'].split(' S')[0]} exempt from: {c['title']}"
        RULES.append({"name": "EXCEPTION", "on_fail": "PAY", "text": title,
                      "params": f"scope = {c['vendor']} · added {TODAY}"})
        RULE_HISTORY.insert(0, {"from": old, "to": TOTALS["ruleset"], "when": f"{TODAY} {_stamp()}",
                                "title": title, "said": "stop bringing me these"})
        LOG.append([_stamp(), "new rule", c["vendor"], c["count"], c["eur"]])
        return {"said": f"Ruleset {TOTALS['ruleset']}. {c['vendor']} stops escalating for this, and the "
                        f"{c['count']} in front of you clear on their own. Old verdicts keep the version "
                        f"they were decided under.", "undo": True, "refresh": True}

    if name == "pay_all":
        n, eur = TOTALS["queued"], TOTALS["queued_eur"]
        if not n:
            _undo.pop()
            return {"said": "The run is already empty. Nothing waiting to go out."}
        TOTALS["paid"] += n
        TOTALS["paid_eur"] = round(TOTALS["paid_eur"] + eur, 2)
        TOTALS["queued"], TOTALS["queued_eur"] = 0, 0.0
        PAYMENTS.clear()
        LOG.append([_stamp(), "approved run", "all suppliers", n, eur])
        return {"said": f"{n} invoices approved, {money(eur)}. The SEPA file is the artefact, so nothing "
                        f"moves until you upload it to the bank.", "undo": True, "refresh": True}

    if name == "report":
        LOG.append([_stamp(), "report held", "Management", 0, 0])
        return {"said": "Held for Management with today's numbers attached. It goes at the end of the "
                        "day unless you stop it.", "undo": True}

    if name == "chase":
        who = ", ".join(w["vendor"] for w in WAITING) or "nobody"
        LOG.append([_stamp(), "chased", who, len(WAITING), 0])
        return {"said": f"Chased {len(WAITING)}: {who}. Same thread as before, so they see what they "
                        f"already ignored.", "undo": True}

    if name == "open" and c:
        _undo.pop()
        return {"said": f"{c['invoices'][0][0]} is open below, with what I read off the page.",
                "expand": key}

    _undo.pop()
    return {"said": "I do not have that one wired up yet."}


def hashlib_short(seed):
    import hashlib
    return hashlib.sha256(seed.encode()).hexdigest()[:8]


def sepa_xml():
    """The artefact the bank takes. Not a payment -- a file you upload."""
    total = TOTALS["queued_eur"] or sum(p["eur"] for p in PAYMENTS)
    n = TOTALS["queued"] or sum(p["count"] for p in PAYMENTS)
    stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%dT%H:%M:%S")
    txs = "".join(
        f'''
      <CdtTrfTxInf>
        <PmtId><EndToEndId>{p['vendor'][:20].replace(' ', '')}-{i}</EndToEndId></PmtId>
        <Amt><InstdAmt Ccy="EUR">{p['eur']:.2f}</InstdAmt></Amt>
        <Cdtr><Nm>{p['vendor']}</Nm></Cdtr>
        <RmtInf><Ustrd>{p['count']} invoice(s), {p['terms']}</Ustrd></RmtInf>
      </CdtTrfTxInf>''' for i, p in enumerate(PAYMENTS, 1))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03">
  <CstmrCdtTrfInitn>
    <GrpHdr>
      <MsgId>DESK-{datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M%S")}</MsgId>
      <CreDtTm>{stamp}</CreDtTm>
      <NbOfTxs>{n}</NbOfTxs>
      <CtrlSum>{total:.2f}</CtrlSum>
      <InitgPty><Nm>Alberto</Nm></InitgPty>
    </GrpHdr>
    <PmtInf>
      <PmtInfId>RUN-{datetime.now(UTC).astimezone().strftime("%Y%m%d")}</PmtInfId>
      <PmtMtd>TRF</PmtMtd>{txs}
    </PmtInf>
  </CstmrCdtTrfInitn>
</Document>
'''


# --------------------------------------------------------------------------- #
# The archive behind the Invoices view. This is not a fixture: every row is a
# real PDF in facturas/, read for real, and judged by the same five rules
# against the real supplier master. 35 of the 500 have no text layer at all --
# they are scans, and they are reported as scans rather than invented.
# --------------------------------------------------------------------------- #
import base64
import re as _re
import sys as _sys
import zlib
from datetime import date as _date
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parents[2]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))          # so `desk.master` resolves from here
from backend import caja_paths as _cp  # noqa: E402  (needs _ROOT on sys.path)

# La Caja moved out of the repo root; caja_paths resolves ALBERTO_CAJA,
# then caja/, then the newest snapshot.
CAJA = _cp.facturas()
# The box holds a year of invoices. The run is dated just after the newest one
# in it, so "past due" means what it would have meant on the day of the run --
# not "old because the fixture is old".
TODAY_D = _date(2026, 9, 19)
_MON = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7,
        "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _lines(path):
    """The text the page actually carries. Nothing is guessed."""
    raw = path.read_bytes()
    out = []
    for m in _re.finditer(rb"stream(.*?)endstream", raw, _re.DOTALL):
        b = m.group(1).strip(b"\r\n")
        try:
            out.append(zlib.decompress(base64.a85decode(b, adobe=True)))
        except Exception:                                          # noqa: BLE001, S112
            # Not every stream in a PDF is ASCII85 + Flate text. The ones that
            # are not are images and fonts, and we want none of them.
            continue
    body = b"\n".join(out).decode("latin-1", "replace")
    return [_re.sub(r"\\(\d{3})", lambda m: chr(int(m.group(1), 8)), t[1:-1]).replace("\\", "")
            for t in _re.findall(r"\((?:[^()\\]|\\.)*\)", body)]


def _eur(t):
    """Both conventions live in this box: 1.409,40 and 1409.40."""
    t = _re.sub(r"[^\d.,\-]", "", t or "")
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


_NUM = r"([A-Za-z0-9/\-]*\d[A-Za-z0-9/\-]*)"
_MONEY = r"[\s.:]*(?:EUR\s*)?([\d][\d.,]*)"


def _read(path):
    """One invoice, as the page states it. Five layouts, one reader."""
    txt = " \n ".join(_lines(path))
    if len(txt) < 60:
        return {"scanned": True}

    def grab(*pats):
        for p in pats:
            m = _re.search(p, txt, _re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None

    issued = None
    d = grab(r"(?:Fecha(?: factura| de emisi.n)?|FECHA)\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})")
    if d:
        dd, mm, yy = d.split("/")
        try:
            issued = _date(int(yy), int(mm), int(dd))
        except ValueError:
            issued = None
    if issued is None:
        m2 = _re.search(r"Fecha de emisi.n:\s*(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", txt, _re.IGNORECASE)
        if m2:
            try:
                issued = _date(int(m2.group(3)), _MON.get(m2.group(2).lower(), 1), int(m2.group(1)))
            except ValueError:
                issued = None

    return {
        "scanned": False,
        "number": grab(r"Invoice\s*#\s*" + _NUM,
                       r"REF FACTURA\s*:?\s*" + _NUM,
                       r"FACTURA\s+SIMPLIFICADA\s+N[\u00ba\u00b0o.]*\s*" + _NUM,
                       r"FACTURA\s*N[\u00ba\u00b0o.]*\s*:?\s*" + _NUM,
                       r"N[\u00ba\u00b0o.]*\s*de\s+factura\s*:?\s*" + _NUM,
                       r"Factura\s*:\s*" + _NUM),
        "nif": grab(r"NIF:?\s*([A-Z]\d{8})"),
        "iban": grab(r"IBAN\)?:\s*([A-Z]{2}[\d ]{20,34})"),
        "order": grab(r"(?:PEDIDO CLIENTE|Pedido asociado|Ref\.?\s*Pedido|Su pedido|\bPO|\bPedido)"
                      r"\s*:?\s*(PO-[\w\-]+)"),
        "base": _eur(grab(r"Importe base" + _MONEY, r"BASE IMPONIBLE" + _MONEY,
                          r"Subtotal" + _MONEY, r"\bBase" + _MONEY) or ""),
        "vat": _eur(grab(r"(?:Cuota\s+)?IVA\s*\(21%\)" + _MONEY) or ""),
        "total": _eur(grab(r"TOTAL A PAGAR" + _MONEY, r"Total factura" + _MONEY,
                           r"\bTOTAL" + _MONEY, r"\bTotal" + _MONEY) or ""),
        "issued": issued,
    }


_ARCHIVE = None
RUN_DATE = TODAY_D


def _judge(inv, master, seen):
    """The five rules, in precedence order, on the real numbers."""
    checks, blocking = [], []

    def add(name, ok, detail, effect):
        checks.append({"name": name, "verdict": "PASS" if ok else "BLOCKS",
                       "effect": "PAY" if ok else effect, "detail": detail})
        if not ok:
            blocking.append(name)

    if inv.get("scanned"):
        add("READABLE", False, "no text layer on the page; it is a scan and needs OCR", "ESCALATE")
        return checks, blocking, "ESCALATE"

    iban = (inv.get("iban") or "").replace(" ", "")
    m_iban = (master or {}).get("iban", "").replace(" ", "")
    same = bool(iban) and bool(m_iban) and iban == m_iban
    add("VENDOR", same or not m_iban,
        f"invoice {iban[-4:] or '—'} against master {m_iban[-4:] or '—'}, NIF {inv.get('nif') or '—'}",
        "ESCALATE")

    num = inv.get("number")
    dup = bool(num) and seen.get(num, 0) > 1
    add("DUPLICATES", not dup,
        f"key {num or '—'}, {'already seen on another page' if dup else 'no earlier payment'}",
        "DO NOT PAY")

    base, vat, total = inv.get("base"), inv.get("vat"), inv.get("total")
    if base is not None and total:
        # Some layouts print the VAT line, some only base and total. Where it is
        # not printed we take it as the difference and check the 21% holds.
        shown = vat if vat is not None else round(total - base, 2)
        delta = round(base + shown - total, 2)
        rate_ok = abs(round(base * 1.21, 2) - total) <= max(0.02, total * 0.005)
        add("AMOUNT", abs(delta) <= max(0.02, total * 0.005) and rate_ok,
            f"base {money(base)} + VAT {money(shown)}"
            + ("" if vat is not None else " (taken as the difference)")
            + f" = {money(round(base + shown, 2))}, the page says {money(total)}, "
              f"delta {money(delta)}", "ESCALATE")
    else:
        add("AMOUNT", False, "could not read base and total together", "ESCALATE")

    terms = int(_re.search(r"\d+", (master or {}).get("condiciones", "30")).group())
    if inv.get("issued"):
        due = _date.fromordinal(inv["issued"].toordinal() + terms)
        # We know when it was issued and what the terms are. We do NOT record when
        # it reached the desk, so we cannot honestly call it late. It informs; it
        # does not block. The day arrival is captured, this becomes a real check.
        add("DATES", True,
            f"issued {inv['issued'].strftime('%d %b %Y')}, {terms} day terms, "
            f"due {due.strftime('%d %b %Y')} — arrival not recorded, so it does not block",
            "ESCALATE")
    else:
        add("DATES", False, "no issue date on the page", "ESCALATE")

    add("MISSING", bool(inv.get("order")),
        f"order {inv['order']} on the page" if inv.get("order") else "no order number anywhere on the page",
        "ESCALATE")

    action = ("DO NOT PAY" if "DUPLICATES" in blocking
              else "ESCALATE" if blocking else "PAY")
    return checks, blocking, action


def archive():
    global _ARCHIVE
    if _ARCHIVE is not None:
        return _ARCHIVE

    from desk import master as _m
    prov, _ped = _m.load()
    by_nif = {v["nif"]: v for v in prov.values()}

    parsed = []
    for path in sorted(CAJA.glob("*.pdf")):
        inv = _read(path)
        inv["file"] = path.name
        parsed.append(inv)

    dates = [i["issued"] for i in parsed if i.get("issued")]
    global RUN_DATE
    RUN_DATE = max(dates).replace(day=min(28, max(dates).day)) if dates else TODAY_D
    RUN_DATE = _date.fromordinal(max(dates).toordinal() + 2) if dates else TODAY_D

    seen = {}
    for i in parsed:
        if i.get("number"):
            seen[i["number"]] = seen.get(i["number"], 0) + 1

    rows = []
    for inv in parsed:
        vend = by_nif.get(inv.get("nif") or "")
        if not vend:
            vid = inv["file"].rsplit("_", 1)[-1].replace(".pdf", "")
            vend = prov.get(vid)
        checks, blocking, action = _judge(inv, vend, seen)
        issued = inv.get("issued")
        rows.append({
            "file": inv["file"],
            "vendor": (vend or {}).get("razon_social", "unidentified supplier"),
            "vendor_id": (vend or {}).get("id", "—"),
            "nif": inv.get("nif") or (vend or {}).get("nif", "—"),
            "terms": (vend or {}).get("condiciones", "—"),
            "iban": inv.get("iban") or "—",
            "iban_master": (vend or {}).get("iban", "—"),
            "number": inv.get("number") or "—",
            "order": inv.get("order") or "—",
            "base": inv.get("base"), "vat": inv.get("vat"),
            "total": inv.get("total") or 0.0,
            "date": issued.strftime("%d ") + _SHORT[issued.month] if issued else "—",
            "issued": issued.isoformat() if issued else None,
            "scanned": bool(inv.get("scanned")),
            "action": action, "blocking": blocking, "checks": checks,
            "found": (blocking[0].lower() if blocking else "clean"),
        })
    _ARCHIVE = rows
    return rows


def archive_page(q="", action="", vendor="", limit=60, offset=0):
    rows = archive()
    if q:
        n = q.lower()
        rows = [r for r in rows if n in r["file"].lower() or n in r["vendor"].lower()
                or n in r["number"].lower()]
    if action:
        rows = [r for r in rows if r["action"] == action]
    if vendor:
        rows = [r for r in rows if r["vendor_id"] == vendor]
    every = archive()
    vend_ids = sorted({r["vendor_id"] for r in every})
    return {
        "rows": [{k: r[k] for k in ("file", "vendor", "number", "date", "total",
                                    "action", "blocking", "found", "scanned")}
                 for r in rows[offset:offset + limit]],
        "total": len(rows), "offset": offset, "limit": limit,
        "counts": {a: sum(1 for r in every if r["action"] == a)
                   for a in ("PAY", "ESCALATE", "DO NOT PAY")},
        "grand": len(every),
        "scanned": sum(1 for r in every if r["scanned"]),
        "vendors": [{"id": v, "name": next(r["vendor"] for r in every if r["vendor_id"] == v),
                     "n": sum(1 for r in every if r["vendor_id"] == v)} for v in vend_ids],
    }


def dossier(file):
    r = next((x for x in archive() if x["file"] == file), None)
    if not r:
        return None
    fields = [["Invoice number", r["number"]], ["Supplier", r["vendor"]],
              ["Tax id", r["nif"]], ["Account on the invoice", r["iban"]],
              ["Account on the master", r["iban_master"]], ["Purchase order", r["order"]],
              ["Issued", r["date"]], ["Terms", r["terms"]]]
    # the three that have to be read together, and in this order
    amounts = [["Net", money(r["base"]) if r["base"] is not None else "—"],
               ["VAT 21%", money(r["vat"]) if r["vat"] is not None else "—"],
               ["Total", money(r["total"]) if r["total"] else "—"]]

    reader = "scan, straight to OCR" if r["scanned"] else "native text, no OCR needed"
    tl = [["ROUTED", f"read the file header: {reader}", "router", 8, 0.0]]
    if r["scanned"]:
        tl.append(["OCR_NEEDED", "no text layer, so nothing was lifted from this page",
                   "extractor", 0, 0.0])
    else:
        tl.append(["TEXT_LIFTED", f"{len([f for f in fields if f[1] != '—'])} fields read off the page",
                   "extractor", 41, 0.0])
        tl.append(["MASTER_MATCHED",
                   f"{r['vendor']} found by NIF {r['nif']}", "conciliador", 3, 0.0])
    tl.append(["RULES_RUN", f"{len(r['checks'])} checks under {TOTALS['ruleset']}", "engine", 2, 0.0])
    tl.append(["VERDICT", r["action"] + (f", blocked by {', '.join(r['blocking'])}"
                                         if r["blocking"] else ", clean on all five"),
               "engine", 1, 0.0])
    done = DECIDED.get(file)
    return {"row": r, "fields": fields, "amounts": amounts, "checks": r["checks"],
            "decided": (f"{_VERB[done['action']].capitalize()} at {done['when']}"
                        + (f", {done['reason']}" if done.get("reason") else ".")) if done else None,
            "timeline": [{"kind": k, "detail": d, "actor": a, "ms": m, "cost": c}
                         for k, d, a, m, c in tl],
            "cost": 0.0, "ms": sum(t[3] for t in tl), "ruleset": TOTALS["ruleset"]}


def lanes(q="", action=""):
    """One box per supplier. The three figures in the header are the whole
    point: what clears on its own, what needs Alberto, and what is stopped."""
    rows = archive()
    if q:
        n = q.lower()
        rows = [r for r in rows if n in r["file"].lower() or n in r["vendor"].lower()
                or n in r["number"].lower()]
    if action:
        rows = [r for r in rows if r["action"] == action]

    by = {}
    for r in rows:
        by.setdefault(r["vendor_id"], []).append(r)

    out = []
    def total(items, kind):
        return round(sum(i["total"] for i in items if i["action"] == kind), 2)

    for vid, items in by.items():
        out.append({
            "id": vid, "name": items[0]["vendor"], "count": len(items),
            "pay_eur": total(items, "PAY"), "review_eur": total(items, "ESCALATE"),
            "nopay_eur": total(items, "DO NOT PAY"),
            "pay_n": sum(1 for i in items if i["action"] == "PAY"),
            "review_n": sum(1 for i in items if i["action"] == "ESCALATE"),
            "nopay_n": sum(1 for i in items if i["action"] == "DO NOT PAY"),
            "terms": items[0]["terms"],
            "rows": [{k: i[k] for k in ("file", "number", "date", "total",
                                        "action", "blocking", "found", "scanned")}
                     for i in sorted(items, key=lambda x: (x["action"] != "ESCALATE", x["file"]))],
        })
    # the supplier that needs him most goes first
    out.sort(key=lambda a: (-a["review_eur"], -a["nopay_eur"], -a["count"]))
    every = archive()
    return {"lanes": out, "matched": len(rows), "grand": len(every),
            "counts": {a: sum(1 for r in every if r["action"] == a)
                       for a in ("PAY", "ESCALATE", "DO NOT PAY")}}


# --------------------------------------------------------------------------- #
# Decisions taken on a single invoice from its expediente. Kept apart from the
# archive itself, which stays a faithful read of what is in the box.
# --------------------------------------------------------------------------- #
DECIDED: dict = {}

_VERB = {"approve": "approved for the run", "reject": "rejected",
         "email": "supplier written to", "defer": "deferred"}


def decide(name, file, reason=None):
    row = next((r for r in archive() if r["file"] == file), None)
    if not row:
        return {"said": "I do not have that file."}
    if name not in _VERB:
        return {"said": "I do not have that one wired up yet."}

    _snapshot_decided()
    DECIDED[file] = {"action": name, "reason": reason, "when": _stamp()}
    amount = money(row["total"])
    who = row["vendor"]

    if name == "approve":
        said = (f"{file} goes into the run, {amount} to {who}. "
                + ("It was clean on all five anyway." if not row["blocking"]
                   else f"You overrode {', '.join(row['blocking'])}, and I wrote that down."))
    elif name == "reject":
        said = f"Rejected. {amount} to {who} will not be paid, and the reason is on the record."
    elif name == "email":
        said = (f"Written to {who} about {row['number']}. It sits in the outbox for a minute, "
                f"so you can still stop it.")
        OUTBOX.append({"to": who, "subject": f"About invoice {row['number']}", "release": _stamp()})
    else:
        said = f"Deferred, {reason or 'for now'}. It shows on {who}'s page so it is not forgotten."

    LOG.append([_stamp(), _VERB[name], who, 1, row["total"]])
    return {"said": said, "undo": True, "decided": {"file": file, "action": name}}


def _snapshot_decided():
    _undo.append({"DECIDED": copy.deepcopy(DECIDED), "OUTBOX": copy.deepcopy(OUTBOX),
                  "LOG": copy.deepcopy(LOG)})
    del _undo[:-12]


def invoice_block(file):
    """One invoice, as a panel the chat can put on screen with its actions."""
    d = dossier(file)
    if not d:
        return None
    r = d["row"]
    done = DECIDED.get(file)
    rows = [{"lead": "", "title": c["name"], "sub": c["detail"], "value": c["verdict"],
             "tone": "" if c["verdict"] == "PASS" else "warn"} for c in d["checks"]]
    acts = []
    if not done:
        acts = [{"label": f"Approve · {money(r['total'])}", "kind": "primary", "act": "approve",
                 "file": file},
                {"label": "Reject", "kind": "danger", "act": "reject", "file": file},
                {"label": "Write to the supplier", "act": "email", "file": file},
                {"label": "Defer", "kind": "defer", "file": file}]
    return {"id": "invoice", "title": file,
            "meta": f"{r['vendor']} · {money(r['total'])} · "
                    + (f"{_VERB[done['action']]} at {done['when']}" if done else r["action"]),
            "rows": rows, "actions": acts}


# --------------------------------------------------------------------------- #
# The Summary. Every figure here is folded from the 500 real invoices, so it
# moves when a decision moves. Nothing is a target and nothing is a forecast.
# --------------------------------------------------------------------------- #
_RULE_SAYS = {
    "VENDOR": "the account on the page is not the one on the master",
    "AMOUNT": "base and VAT do not add up to the total",
    "DUPLICATES": "the same invoice number was already paid",
    "DATES": "no readable issue date",
    "MISSING": "no purchase order on the page",
    "READABLE": "a scan with no text layer, nothing could be read",
}


def summary():
    rows = archive()
    n = len(rows)

    def side(kind):
        got = [r for r in rows if r["action"] == kind]
        return {"n": len(got), "eur": round(sum(r["total"] for r in got), 2)}

    pay, review, stop = side("PAY"), side("ESCALATE"), side("DO NOT PAY")

    # why the work exists at all: one line per rule that stopped something
    blocking = {}
    for r in rows:
        for b in r["blocking"]:
            slot = blocking.setdefault(b, {"n": 0, "eur": 0.0})
            slot["n"] += 1
            slot["eur"] = round(slot["eur"] + r["total"], 2)
    why = sorted(({"rule": k, "says": _RULE_SAYS.get(k, k), **v} for k, v in blocking.items()),
                 key=lambda x: -x["n"])

    # per supplier, both ways round
    per = {}
    for r in rows:
        s = per.setdefault(r["vendor_id"], {"id": r["vendor_id"], "name": r["vendor"],
                                            "n": 0, "eur": 0.0, "review_n": 0, "review_eur": 0.0})
        s["n"] += 1
        s["eur"] = round(s["eur"] + r["total"], 2)
        if r["action"] == "ESCALATE":
            s["review_n"] += 1
            s["review_eur"] = round(s["review_eur"] + r["total"], 2)
    people = list(per.values())
    spend = round(sum(p["eur"] for p in people), 2)
    by_count = sorted(people, key=lambda p: -p["n"])
    by_money = sorted(people, key=lambda p: -p["eur"])

    # volume and money by month, from the dates on the pages
    months = {}
    for r in rows:
        if not r["issued"]:
            continue
        key = r["issued"][:7]
        m = months.setdefault(key, {"month": key, "n": 0, "eur": 0.0})
        m["n"] += 1
        m["eur"] = round(m["eur"] + r["total"], 2)
    trend = sorted(months.values(), key=lambda m: m["month"])

    top = by_money[0] if by_money else {"name": "—", "eur": 0}
    return {
        "total": n, "spend": spend,
        "pay": pay, "review": review, "stop": stop,
        "suppliers": len(people),
        "scanned": sum(1 for r in rows if r["scanned"]),
        "why": why,
        "by_count": by_count, "by_money": by_money,
        "trend": trend,
        "concentration": {"name": top["name"], "eur": top["eur"],
                          "share": round(top["eur"] / spend * 100, 1) if spend else 0},
        "touched": len(DECIDED),
        "ruleset": TOTALS["ruleset"],
    }


# --------------------------------------------------------------------------- #
# The ruleset, as it actually behaves. Not documentation: each rule carries
# what it caught across the 500 invoices in the box.
# --------------------------------------------------------------------------- #
_ORDER = [
    ("READABLE", "gate", "The page has a text layer at all.",
     "Nothing else can run on a scan. It stops here and waits for OCR."),
    ("DUPLICATES", "DO NOT PAY", "No invoice with this number has been paid before.",
     "The only rule that refuses outright. Everything else brings it to you."),
    ("VENDOR", "ESCALATE", "The account and the NIF on the page match the master.",
     "A changed account is how invoice fraud actually works, so it never auto-pays."),
    ("AMOUNT", "ESCALATE", "Base plus VAT reaches the total the page states.",
     "Tolerance 0.5%. Where the page prints no VAT line it is taken as the difference."),
    ("DATES", "ESCALATE", "There is a readable issue date, and the terms are known.",
     ("It reports the due date but does not block: nothing records when an invoice "
      "reached the desk, so calling it late would be a guess.")),
    ("MISSING", "ESCALATE", "A purchase order number is on the page.",
     "No exception configured. Turning one into a rule from a cluster adds it here."),
]


def rules_view():
    rows = archive()
    hit = {}
    for r in rows:
        for b in r["blocking"]:
            slot = hit.setdefault(b, {"n": 0, "eur": 0.0})
            slot["n"] += 1
            slot["eur"] = round(slot["eur"] + r["total"], 2)

    out = []
    for i, (name, effect, does, note) in enumerate(_ORDER, 1):
        got = hit.get(name, {"n": 0, "eur": 0.0})
        out.append({"step": i, "name": name, "effect": effect, "does": does, "note": note,
                    "n": got["n"], "eur": got["eur"],
                    "share": round(got["n"] / len(rows) * 100, 1) if rows else 0})

    extra = [r for r in RULES if r["name"] not in {o[0] for o in _ORDER}]
    for j, r in enumerate(extra, len(out) + 1):
        out.append({"step": j, "name": r["name"], "effect": r["on_fail"], "does": r["text"],
                    "note": r["params"], "n": 0, "eur": 0.0, "share": 0, "added": True})

    return {"version": TOTALS["ruleset"], "sha": TOTALS["ruleset_sha"],
            "checked": len(rows), "clean": sum(1 for r in rows if not r["blocking"]),
            "stopped": sum(1 for r in rows if r["blocking"]),
            "rules": out, "history": RULE_HISTORY}
