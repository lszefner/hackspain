"""The conversational surface. Deterministic intent routing, rendered replies.

Replies are BLOCKS, not prose: a cluster card carries its own buttons, a trace
carries its own timeline. The chat is the command line of the system; its
answers are live objects.

No model call is required for anything here. An LLM can later be layered on top
for phrasing and for rules it cannot parse (see rules.parse), but the system
keeps working with the provider down -- same posture as the ingestion pipeline.
"""
from __future__ import annotations

import re
import unicodedata

from desk import agents, explain, llm, mail, rules, state
from desk.ledger import Ledger


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode(
        "ascii", "ignore").decode().lower()


def ingestion_status(led: Ledger) -> dict:
    events = led.by_kind("INGESTION_STARTED", "INGESTION_FINISHED", "INGESTION_FAILED")
    if not events:
        return {"state": "never_run"}
    last = events[-1]
    state_name = {"INGESTION_STARTED": "running",
                  "INGESTION_FINISHED": "finished",
                  "INGESTION_FAILED": "failed"}[last["kind"]]
    return {"state": state_name, "ts": last["ts"], **last["payload"]}


def brief(led: Ledger, *, demo: bool = False) -> dict:
    rows = state.invoices(led)
    s = state.summary(led, rows)
    cl = state.clusters(rows)
    ob = agents.outbox(led)
    return {"summary": s, "clusters": len(cl), "outbox": len(ob),
            "llm": llm.status(), "mail": mail.status(),
            "ingestion": ingestion_status(led), "demo": demo}


def _clusters_block(led: Ledger, rows=None) -> dict:
    rows = rows if rows is not None else state.invoices(led)
    cl = state.clusters(rows)
    return {"type": "clusters", "clusters": [{
        "id": c["id"], "label": c["label"], "rule": c["rule"], "vendor": c["vendor"],
        "vendor_email": c["vendor_email"], "count": c["count"], "total": c["total"],
        "file_ids": c["file_ids"], "recommendation": c["recommendation"],
        "finding": (c["items"][0]["investigation"] or {}).get("finding"),
        "proposed_action": (c["items"][0]["investigation"] or {}).get("proposed_action"),
        "items": [{"file_id": i["file_id"], "total": i["invoice"]["total"],
                   "number": i["invoice"]["invoice_number"], "date": i["invoice"]["date"]}
                  for i in c["items"]],
    } for c in cl]}


def _table(rows: list[dict], limit=60) -> dict:
    return {"type": "invoices", "rows": [{
        "file_id": r["file_id"], "vendor": r["invoice"]["vendor"],
        "number": r["invoice"]["invoice_number"], "total": r["invoice"]["total"],
        "date": r["invoice"]["date"], "decision": r["decision"], "status": r["status"],
        "blocking": [c["canonical"] for c in r["blocking"]],
    } for r in rows[:limit]], "truncated": max(0, len(rows) - limit), "total": len(rows)}


def trace_block(led: Ledger, file_id: str) -> dict | None:
    row = state.get(led, file_id)
    if not row:
        return None
    nodes = explain.timeline(led, file_id)
    return {"type": "trace", "file_id": file_id, "row": {
        "decision": row["decision"], "status": row["status"], "invoice": row["invoice"],
        "ruleset_version": row["ruleset_version"], "checks": row["checks"],
        "cost_usd": row["cost_usd"], "ms": row["ms"], "history": row["history"],
        "investigation": row["investigation"], "payment": row["payment"],
        "emails": row["emails"], "erp_retries": row["erp_retries"],
        "used_fallback": row["used_fallback"],
    }, "why": explain.why(row), "how": explain.how(row, nodes), "timeline": nodes}


def _find_file(led: Ledger, text: str) -> str | None:
    m = re.search(r"(factura_\d+\.pdf|factura_\d+|\bF-\d{6,}\b)", text, re.IGNORECASE)
    if not m:
        return None
    tok = m.group(1)
    if tok.lower().startswith("factura_"):
        fid = tok if tok.endswith(".pdf") else tok + ".pdf"
        return fid if state.get(led, fid) else None
    for r in state.invoices(led):
        if fold(r["invoice"]["invoice_number"]) == fold(tok):
            return r["file_id"]
    return None


# --------------------------------------------------------------------------- #
def respond(led: Ledger, text: str, *, thread_id="main") -> dict:
    """Deterministic answer first; the model may only rephrase the computed
    text -- and only when there is no trace block to keep verbatim."""
    result = _respond(led, text, thread_id=thread_id)
    if any(b.get("type") == "trace" for b in result.get("blocks", [])):
        return result
    from desk import llm_contract
    result["text"] = llm_contract.phrase(led, "chat", result["text"],
                                       thread_id=thread_id)
    return result


def _respond(led: Ledger, text: str, *, thread_id="main") -> dict:
    t = fold(text.strip())
    rows = state.invoices(led)
    s = state.summary(led, rows)

    # -- a specific invoice -------------------------------------------------
    fid = _find_file(led, text)
    if fid:
        tb = trace_block(led, fid)
        return {"text": tb["why"], "blocks": [tb]}

    # -- a rule change (checked before generic keywords) --------------------
    if any(k in t for k in ("regla", "norma", "a partir de ahora", "quiero que", "cambia",
                            "tolerancia", "segunda firma", "exime", "exencion", "por debajo de",
                            "por encima de")):
        prop = rules.propose(led, text, thread_id=thread_id)
        if prop:
            bt = prop["backtest"]
            verb = "Es una regla nueva" if prop["new_rule"] else "Modifica un parametro existente"
            return {"text": (f"{verb}. Antes de aplicarla la he simulado contra las "
                             f"{bt['evaluated']} facturas ya decididas: {len(bt['flips'])} cambian "
                             f"de veredicto ({bt['loosened']} se abren a PAGAR por "
                             f"{agents.money(bt['loosened_eur'])}, {bt['tightened']} se cierran "
                             f"por {agents.money(bt['tightened_eur'])}). Nada se aplica hasta que "
                             f"lo apruebes."),
                    "blocks": [{"type": "proposal", "proposal": {
                        k: prop[k] for k in ("title", "rationale", "params", "from_version",
                                             "to_version", "new_rule", "source_text", "seq",
                                             "diff")},
                        "backtest": bt}]}
        if "regla" in t or "norma" in t:
            return {"text": ("No he sabido convertir eso en un cambio de parametro, y no voy a "
                             "inventarme una regla. Pruebalo mas concreto, por ejemplo: "
                             "'toda factura por encima de 10000 EUR necesita segunda firma', "
                             "'sube la tolerancia de importe a 25 EUR', o "
                             "'Papeleria Ruzafa por debajo de 200 EUR sin pedido, paga'."),
                    "blocks": [{"type": "rules", "ruleset": rules.current(led)}]}

    # -- report -------------------------------------------------------------
    if any(k in t for k in ("informe", "cierre", "reporte", "resumen del dia", "report")):
        rep = agents.daily_report(led)
        return {"text": "Este es el cierre del dia. Lo puedo dejar en la bandeja para quien me digas.",
                "blocks": [{"type": "report", "subject": rep["subject"], "body": rep["body"],
                            "to": mail.resolve_recipient(mail.status()["report_to"]) or ""}]}

    # -- outbox / emails ----------------------------------------------------
    if any(k in t for k in ("email", "correo", "bandeja", "outbox", "enviad")):
        ob = agents.outbox(led)
        return {"text": (f"Tengo {len(ob)} correo(s) retenidos. Salen solos cuando vence su "
                         f"ventana; hasta entonces los puedes cancelar uno a uno."),
                "blocks": [{"type": "outbox", "emails": ob}]}

    # -- duplicates ---------------------------------------------------------
    if "duplicad" in t:
        dup = [r for r in rows if any(c["canonical"] == "DUPLICATES" for c in r["blocking"])]
        hard = [r for r in dup if r["decision"] == "NO_PAGAR"]
        return {"text": (f"He encontrado {len(dup)} coincidencias: {len(hard)} duras "
                         f"(misma factura del mismo proveedor ya liquidada en el ERP, "
                         f"{agents.money(s['duplicates_saved'])} que se habrian pagado dos veces) "
                         f"y {len(dup) - len(hard)} blandas, que solo coinciden en importe y fecha "
                         f"y por eso escalan en vez de bloquearse."),
                "blocks": [_table(dup)]}

    # -- cost ---------------------------------------------------------------
    if any(k in t for k in ("coste", "cuesta", "cost", "precio", "token")):
        return {"text": (f"Llevo {s['cost_usd']:.4f} USD en {s['total']} facturas, "
                         f"{s['cost_per_invoice']:.5f} USD por factura. Ahi dentro estan las "
                         f"extracciones, las normalizaciones, las conciliaciones con el ERP "
                         f"({s['erp_retries']} reintentos absorbidos) y las "
                         f"{s['auto_resolved']} investigaciones. El motor de reglas no cuesta "
                         f"nada: es determinista y corre en local."),
                "blocks": [{"type": "metrics", "summary": s}]}

    # -- remesa / payments --------------------------------------------------
    if any(k in t for k in ("remesa", "pago", "pagos", "sepa", "banco")):
        paid = [r for r in rows if r["status"] == "paid"]
        queued = [r for r in rows if r["status"] == "queued"]
        return {"text": (f"{len(paid)} facturas pagadas y {len(queued)} en cola por "
                         f"{agents.money(s['queued_total'])}. La remesa se genera en formato "
                         f"SEPA pain.001, que es lo que se sube al banco."),
                "blocks": [{"type": "payments", "paid": len(paid), "queued": len(queued),
                            "queued_total": s["queued_total"]}, _table(queued, 25)]}

    # -- the queue ----------------------------------------------------------
    if any(k in t for k in ("escalad", "pendient", "necesit", "atencion", "que hago", "cola",
                            "revisar", "hola", "buenos dias", "buenas", "empez", "criterio",
                            "patron", "grupo")):
        agents.investigate_all(led)
        rows = state.invoices(led)
        s = state.summary(led, rows)
        cb = _clusters_block(led, rows)
        n = len(cb["clusters"])
        return {"text": (f"Tienes {s['escalated']} facturas escaladas, pero no son "
                         f"{s['escalated']} decisiones: son {n} patrones. He investigado las "
                         f"{s['auto_resolved']} y en cada grupo te digo lo que haria."),
                "blocks": [cb]}

    # -- vendor search ------------------------------------------------------
    for r in rows:
        parts = fold(r["invoice"].get("vendor") or "").split()
        first = parts[0] if parts else ""
        if len(first) > 4 and first in t:
            hits = [x for x in rows
                    if (fold(x["invoice"].get("vendor") or "").split() or [""])[0] == first]
            tot = sum(x["invoice"]["total"] or 0 for x in hits)
            return {"text": (f"{len(hits)} facturas de {r['invoice']['vendor']} por "
                             f"{agents.money(tot)}."), "blocks": [_table(hits)]}

    # -- fallback -----------------------------------------------------------
    return {"text": ("Puedo ayudarte con: lo que necesita tu atencion, por que una factura "
                     "concreta acabo como acabo (dime su nombre o su numero), los duplicados que "
                     "he parado, la remesa y los pagos, el coste de proceso, el cierre del dia, "
                     "o cambiar una regla diciendomela en tu idioma."),
            "blocks": [{"type": "metrics", "summary": s}]}
