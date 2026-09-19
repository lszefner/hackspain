"""Plain-language WHY / HOW, rendered FROM THE LEDGER.

Hard rule: this module never re-reads the PDF and never re-reasons. It reads
the events that were recorded at decision time and puts them into sentences.
If it had to think, the explanation could disagree with the decision -- and an
audit trail that can disagree with itself is not an audit trail.
"""
from __future__ import annotations

from desk.agents import money
from desk.ledger import Ledger

RULE_ES = {
    "VENDOR": "proveedor (alta, NIF, IBAN)",
    "DUPLICATES": "duplicados",
    "AMOUNT": "importes (base, IVA, pedido)",
    "DATES": "fechas y condiciones de pago",
    "MISSING": "campos obligatorios",
    "AUTHORIZATION": "autorizacion por importe",
}
DECISION_ES = {"PAGAR": "PAGAR", "ESCALAR": "ESCALAR", "NO_PAGAR": "NO PAGAR"}


def timeline(led: Ledger, file_id: str) -> list[dict]:
    """The decision chain, one node per step, expandable to the raw event."""
    nodes, rules_bucket = [], []

    def flush_rules(up_to):
        if not rules_bucket:
            return
        nodes.append({"seq": rules_bucket[0]["seq"], "ts": rules_bucket[0]["ts"],
                      "kind": "RULES", "actor": "engine",
                      "cost_usd": sum(n["cost_usd"] for n in rules_bucket),
                      "ms": sum(n["ms"] for n in rules_bucket),
                      "title": f"Reglas {up_to}",
                      "detail": " · ".join(f"{n['payload']['canonical']} {n['payload']['verdict']}"
                                           for n in rules_bucket),
                      "payload": {"checks": [n["payload"] for n in rules_bucket],
                                  "events": list(rules_bucket)},
                      "ruleset_version": up_to})
        rules_bucket.clear()

    for ev in led.for_file(file_id):
        p, kind = ev["payload"], ev["kind"]
        node = {"seq": ev["seq"], "ts": ev["ts"], "kind": kind, "actor": ev["actor"],
                "cost_usd": ev["cost_usd"], "ms": ev["ms"], "payload": p,
                "ruleset_version": ev["ruleset_version"]}
        if kind == "FILE_RECEIVED":
            node["title"] = "Fichero recibido"
            node["detail"] = f"{p.get('pages')} pag · {p.get('bytes', 0) // 1024} KB · sha {p.get('sha256', '')[:12]}"
        elif kind == "PROVIDER_FAILED":
            node["title"] = "Proveedor caido"
            node["detail"] = (f"{p.get('provider')} devolvio {p.get('error_code')} · {p.get('action')}"
                              + (" · hubo un fallo de proveedor y una extraccion posterior registrada"
                                 if p.get("recovered") else ""))
        elif kind == "EXTRACTED":
            node["title"] = "Extraccion"
            node["detail"] = (f"{p.get('provider')}/{p.get('model')} · confianza {p.get('confidence')}"
                              f" · {p.get('attempts')} intento(s)")
        elif kind == "NORMALIZED":
            node["title"] = "Normalizacion"
            node["detail"] = " · ".join(f"{c['field']}: {c['from']} -> {c['to']}"
                                        for c in p.get("changes", []))
        elif kind == "ERP_RECONCILED":
            node["title"] = "Conciliacion con el ERP"
            node["detail"] = (f"pedido {p.get('pedido') or 'n/d'} · asiento {p.get('asiento') or 'n/d'}"
                              f" · estado {p.get('erp_estado')}"
                              + (f" · {p.get('retries')} reintento(s)"
                                 + (f" ({', '.join(p.get('errors') or [])})"
                                    if p.get("errors") else "")
                                 if p.get("retries") else ""))
        elif kind == "RULE_EVALUATED":
            rules_bucket.append(node)
            continue
        elif kind == "VERDICT":
            flush_rules(ev["ruleset_version"])
            node["title"] = f"Veredicto {DECISION_ES.get(p.get('decision'), p.get('decision'))}"
            node["detail"] = ("reproceso: antes " + p["reprocessed_from"] + " · " + p.get("reason_for_reprocess", "")
                              if p.get("reprocessed_from") else
                              "determinista · " + ", ".join(p.get("driven_by", [])))
        elif kind == "INVESTIGATED":
            node["title"] = "Investigacion del agente"
            node["detail"] = p.get("finding", "")
        elif kind in ("HUMAN_APPROVED", "HUMAN_REJECTED"):
            node["title"] = "Aprobado por Alberto" if kind == "HUMAN_APPROVED" else "Rechazado por Alberto"
            node["detail"] = f"lote de {p.get('batch_size', 1)} · {p.get('approval_id', '')}"
        elif kind == "PAYMENT_REGISTERED":
            node["title"] = "Pago registrado"
            node["detail"] = f"{money(p.get('amount', 0))} · remesa {p.get('remesa')} · clave {p.get('idempotency_key')}"
        elif kind == "PAYMENT_REFUSED":
            node["title"] = "Pago rechazado por el tesorero"
            node["detail"] = f"{p.get('reason')} · pedido por {p.get('requested_by')}"
        elif kind == "PAYMENT_BLOCKED":
            node["title"] = "Pago duplicado bloqueado"
            node["detail"] = f"{p.get('reason')} · clave {p.get('idempotency_key')}"
        elif kind == "EMAIL_QUEUED":
            node["title"] = "Email en cola"
            node["detail"] = f"{p.get('to')} · {p.get('subject')} · sale a las {p.get('release_at', '')[11:19]}"
        elif kind == "EMAIL_SENT":
            node["title"] = "Email enviado"
            node["detail"] = f"{p.get('to')} · {p.get('subject')}"
        elif kind == "EMAIL_CANCELLED":
            node["title"] = "Email cancelado"
            node["detail"] = f"{p.get('to')} · {p.get('template')}"
        elif kind == "EMAIL_FAILED":
            node["title"] = "Email fallido"
            node["detail"] = f"{p.get('to')} · {p.get('subject')} · {p.get('reason')}"
        elif kind == "EMAIL_UNCERTAIN":
            node["title"] = "Entrega de email incierta"
            node["detail"] = (f"{p.get('to')} · {p.get('subject')} · {p.get('reason')} "
                              f"-- comprobar manualmente, no se reintenta solo")
        elif kind == "EMAIL_DISPATCH_STARTED":
            node["title"] = "Envio de email iniciado"
            node["detail"] = f"{p.get('to')} · {p.get('subject')} · transporte {p.get('transport')}"
        elif kind == "LLM_CALL":
            node["title"] = "Llamada LLM"
            node["detail"] = (f"{p.get('purpose')} · {p.get('provider')}/{p.get('model')} "
                              f"· intento {p.get('attempt')} · {p.get('status')}"
                              + (f" · {p.get('error_code')}" if p.get("error_code") else ""))
        else:
            node["title"] = kind
            node["detail"] = ""
        nodes.append(node)
    flush_rules(None)
    return nodes


def why(row: dict) -> str:
    """One paragraph, mortal language, every number taken from the trace."""
    inv, dec = row["invoice"], row["decision"]
    head = (f"{row['file_id']} - {inv['vendor']}, {money(inv['total'])}, "
            f"factura {inv['invoice_number']} del {inv['date']}.")

    if dec == "PAGAR":
        # Built from the reasons that were actually recorded, never from a canned
        # paragraph: if a rule passed thanks to an exemption, the sentence says so.
        reasons = "; ".join(f"{RULE_ES.get(c['canonical'], c['canonical'])}: {c['reason']}"
                            for c in row["checks"])
        body = (f"Las {len(row['checks'])} reglas del ruleset {row['ruleset_version']} "
                f"permiten el pago: {reasons}. Conforme a sus parametros, "
                f"el veredicto es PAGAR.")
    else:
        parts = []
        for c in row["blocking"]:
            effect = c.get("effect", "ESCALAR" if c["verdict"] != "PASS" else "PAGAR")
            verdict = "bloquea el pago" if effect == "NO_PAGAR" else "obliga a revisar"
            parts.append(f"la regla de {RULE_ES.get(c['canonical'], c['canonical'])} {verdict} "
                         f"porque {c['reason']}")
        rest = len(row["checks"]) - len(row["blocking"])
        body = ("Se queda en " + DECISION_ES.get(dec, dec) + " porque " + "; y ".join(parts) +
                f". Las otras {rest} reglas pasan. Por precedencia "
                f"(NO_PAGAR > ESCALAR > PAGAR) manda la mas restrictiva.")

    tail = (f" Coste de procesarla: {row['cost_usd']:.4f} USD en {row['ms'] / 1000:.1f} s"
            + (f", con {row['erp_retries']} reintento(s) absorbidos del ERP" if row["erp_retries"] else "")
            + (", y con un fallo de proveedor seguido de una extraccion registrada"
               if row["used_fallback"] else "") + ".")

    hist = ""
    if len(row["history"]) > 1:
        first, last = row["history"][0], row["history"][-1]
        hist = (f" Ojo: no siempre fue asi. Salio {DECISION_ES.get(first['decision'])} bajo "
                f"{first['ruleset_version']} y paso a {DECISION_ES.get(last['decision'])} bajo "
                f"{last['ruleset_version']}. El historial registrado conserva ambos "
                f"veredictos; no se ha sobrescrito.")

    inv_note = ""
    if row["investigation"]:
        i = row["investigation"]
        inv_note = " El investigador anadio: " + i["finding"]
        if i.get("recommendation"):
            inv_note += f" Recomienda {DECISION_ES.get(i['recommendation'], i['recommendation'])}."
    pay_note = ""
    if row["payment"]:
        pay_note = (f" Pagada en la remesa {row['payment']['remesa']} con clave de idempotencia "
                    f"{row['payment']['idempotency_key']}, asi que un reintento del lote no la "
                    f"volveria a pagar.")
    return head + " " + body + tail + hist + inv_note + pay_note


def how(row: dict, nodes: list[dict]) -> str:
    """The HOW: the path the file walked, step by step."""
    steps = [n for n in nodes if n["kind"] != "RULE_EVALUATED"]
    return (f"Recorrido: {len(steps)} pasos registrados, "
            f"{sum(1 for n in nodes if n['kind'] == 'RULES')} evaluacion(es) de reglas, "
            f"{len(row['history'])} veredicto(s) en el historial"
            + (f" (cambio por reproceso: {' -> '.join(h['decision'] for h in row['history'])})"
               if len(row["history"]) > 1 else "") + ".")
