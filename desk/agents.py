"""The agentic layer. Agents investigate, draft, execute and legislate.

None of them DECIDE: the verdict stays with the deterministic ruleset. An agent
may recommend a verdict, gather the evidence for it and prepare the action, but
the flip only happens when a human approves or a rule changes -- and either way
it lands in the ledger as its own event.
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

from desk import mail, state
from desk.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
UNDO_SECONDS = 60

_PEDIDOS: dict | None = None


def _today() -> date:
    return datetime.now(UTC).date()


def pedidos() -> dict:
    global _PEDIDOS
    if _PEDIDOS is None:
        from desk.seed import load_master
        _PEDIDOS = load_master()[1]
    return _PEDIDOS


def money(v) -> str:
    return f"{float(v or 0):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") + " EUR"


# --------------------------------------------------------------------------- #
# INVESTIGADOR -- tries to CLOSE an escalation instead of parking it
# --------------------------------------------------------------------------- #
def investigate(led: Ledger, row: dict, *, thread_id=None) -> dict:
    started = time.monotonic()
    inv = row["invoice"]
    block = row["blocking"][0] if row["blocking"] else None
    rule = block["canonical"] if block else "OTHER"
    d = block.get("detail", {}) if block else {}
    finding, recommendation, evidence, action = "", None, [], None

    if rule == "MISSING" and d.get("absent") != ["pedido"] \
            or rule == "VENDOR" and (not d.get("vendor_found")
                                     or (d.get("nif_master") and d.get("nif") != d.get("nif_master"))
                                     or d.get("iban_invoice") == d.get("iban_master")) \
            or rule == "AMOUNT" and (d.get("delta") is None or float(d["delta"]) == 0) \
            or rule == "DATES":
        finding = block["reason"]
        evidence = [{"label": "detalle registrado", "value": d}]

    elif rule == "DUPLICATES":
        twin_id = (d.get("matches") or [None])[0]
        twin = state.get(led, twin_id) if twin_id and twin_id.endswith(".pdf") else None
        if twin:
            same_pedido = twin["invoice"].get("pedido") == inv.get("pedido")
            evidence = [{"label": f"factura gemela {twin_id}", "file_id": twin_id},
                        {"label": "clave blanda (importe, fecha)", "value": d.get("soft_key")}]
            if not same_pedido:
                finding = (f"Coinciden importe ({money(inv['total'])}) y fecha "
                           f"({inv['date']}), pero el pedido es distinto: {inv.get('pedido')} "
                           f"frente a {twin['invoice'].get('pedido')}. No puedo verificar que "
                           f"las lineas no se solapen; un humano deberia compararlas.")
                if len(row["blocking"]) == 1 and inv.get("pedido") \
                        and twin["invoice"].get("pedido"):
                    recommendation = "PAGAR"
                else:
                    finding += " Es una pista para revisar, no una autorizacion."
            else:
                finding = (f"Mismo pedido {inv.get('pedido')}, mismo importe y misma fecha que "
                           f"{twin_id}. Esto si parece un duplicado real.")
                recommendation = "NO_PAGAR"
        else:
            finding = (f"Coincide importe y fecha con {twin_id}, pero no he podido recuperar la "
                       f"factura gemela para comparar pedidos. No me pronuncio.")
            evidence = [{"label": "clave blanda", "value": d.get("soft_key")}]

    elif rule == "MISSING":
        want = round(float(inv["total"]), 2)
        hits = [p for p in pedidos().values()
                if p.get("proveedor_id") == inv["vendor_id"]
                and abs(round(float(p.get("importe_total") or 0), 2) - want) <= 0.01]
        if len(hits) == 1:
            hit = hits[0]
            finding = (f"La factura no trae pedido, pero en el maestro hay exactamente uno de "
                       f"{inv['vendor']} por {money(want)}: {hit['pedido']}. Encaja al centimo.")
            recommendation = "PAGAR"
            evidence = [{"label": f"pedido {hit['pedido']} en el maestro", "value": hit}]
        elif len(hits) > 1:
            finding = (f"Sin numero de pedido y hay {len(hits)} pedidos de {inv['vendor']} por "
                       f"{money(want)} en el maestro: no puedo saber cual es. "
                       f"Hay que pedirselo al proveedor.")
            action = "email:request_po"
            evidence = [{"label": "pedidos candidatos", "value": [p["pedido"] for p in hits]}]
        else:
            finding = (f"Sin numero de pedido y no encuentro ninguno de {inv['vendor']} por "
                       f"{money(want)}. Hay que pedirselo al proveedor.")
            action = "email:request_po"
            evidence = [{"label": "campos ausentes", "value": d.get("absent")}]

    elif rule == "VENDOR":
        first_seq = (row["history"] or [{}])[0].get("seq", 0)
        prior = [r for r in state.invoices(led)
                 if r["invoice"]["vendor_id"] == inv["vendor_id"]
                 and r["file_id"] != row["file_id"]
                 and (r["history"] or [{}])[0].get("seq", 0) < first_seq
                 and r["invoice"].get("iban") == d.get("iban_master")]
        old = d.get("iban_master")
        finding = (f"El IBAN de la factura ({d.get('iban_invoice')}) no es el del maestro ({old}). "
                   f"{len(prior)} facturas anteriores de {inv['vendor']} usaban el del maestro. "
                   f"Un cambio de cuenta no verificado es el vector clasico de fraude: no lo resuelvo yo.")
        action = "email:confirm_iban"
        evidence = [{"label": "IBAN en la factura", "value": d.get("iban_invoice")},
                    {"label": "IBAN en el maestro", "value": old},
                    {"label": "facturas previas con el IBAN del maestro", "value": len(prior)}]

    elif rule == "AMOUNT":
        delta = abs(float(d.get("delta") or 0))
        total = float(inv["total"] or 0)
        pct = delta / max(total, 1) * 100
        finding = (f"El total ({money(total)}) no cuadra con el pedido {inv.get('pedido')} "
                   f"({money(float(d.get('pedido_importe') or 0))}): {money(delta)} de diferencia, un "
                   f"{pct:.1f}%. " + ("Por debajo del 1%, compatible con portes o redondeo."
                                      if pct < 1 else "Demasiado para ser un redondeo."))
        action = None if pct < 1 else "email:query_amount"
        evidence = [{"label": "total factura", "value": total},
                    {"label": "importe pedido", "value": d.get("pedido_importe")},
                    {"label": "diferencia", "value": d.get("delta")}]

    else:
        finding = block["reason"] if block else "Sin regla bloqueante identificable."
        if block:
            evidence = [{"label": "detalle registrado", "value": d}]

    from desk import llm_contract
    finding = llm_contract.phrase(led, "investigator", finding,
                                  file_id=row["file_id"], thread_id=thread_id)
    result = {"rule": rule, "finding": finding, "recommendation": recommendation,
              "evidence": evidence, "proposed_action": action}
    seq = led.record("INVESTIGATED", file_id=row["file_id"], actor="agent:investigador",
                     thread_id=thread_id, cost_usd=0,
                     ms=int((time.monotonic() - started) * 1000), **result)
    result["seq"] = seq
    return result


def investigate_all(led: Ledger, *, thread_id=None) -> int:
    rows = [r for r in state.invoices(led) if r["status"] == "escalated" and not r["investigation"]]
    for row in rows:
        investigate(led, row, thread_id=thread_id)
    return len(rows)


# --------------------------------------------------------------------------- #
# EMAIL -- drafted by agents, held in an outbox, cancellable
# --------------------------------------------------------------------------- #
def _tpl(kind: str, inv: dict, extra: dict | None = None) -> tuple[str, str]:
    extra = extra or {}
    n, v, t = inv["invoice_number"], inv["vendor"], money(inv["total"])
    if kind == "payment_notice":
        return (f"Aviso de pago - factura {n}",
                (f"Estimados {v}:\n\nHemos preparado la factura {n} por {t} para la remesa "
                f"{extra.get('remesa', '-')}. Fecha prevista: "
                f"{extra.get('value_date', _today().isoformat())}. "
                f"Este aviso confirma la preparacion del fichero SEPA, no la ejecucion "
                f"bancaria.\n\nAdministracion"))
    if kind == "request_po":
        return (f"Falta numero de pedido - factura {n}",
                (f"Estimados {v}:\n\nHemos recibido su factura {n} por {t}, pero no consta el numero "
                f"de pedido y no hemos podido localizarlo en nuestro sistema.\n\n"
                f"Para poder tramitar el pago les agradeceriamos que nos indiquen la referencia del "
                f"pedido correspondiente.\n\nQuedamos a la espera.\n\nAdministracion"))
    if kind == "confirm_iban":
        return (f"Confirmacion de cuenta bancaria - factura {n}",
                (f"Estimados {v}:\n\nEn su factura {n} figura una cuenta distinta a la que tenemos "
                f"registrada. Por seguridad hemos pausado el pago hasta confirmarlo.\n\n"
                f"Les rogamos confirmen por un canal conocido si han cambiado de entidad.\n\n"
                f"Administracion"))
    if kind == "query_amount":
        return (f"Discrepancia de importe - factura {n}",
                (f"Estimados {v}:\n\nSu factura {n} asciende a {t}, mientras que el pedido "
                f"{inv.get('pedido')} recoge {money(extra.get('pedido_importe') or 0)}.\n\n"
                f"Podrian aclararnos la diferencia? En cuanto la confirmemos tramitamos el pago.\n\n"
                f"Administracion"))
    if kind == "pause_review":
        items = extra.get("items") or []
        lines = "\n".join(f"  - {i['invoice_number']} por {money(i['total'])}"
                          for i in items) or f"  - {n} por {t}"
        return (f"Facturas en revision - {v}",
                (f"Estimados {v}:\n\nHemos pausado estas facturas por {extra.get('cause', 'una revision')}:\n\n"
                 f"{lines}\n\nPueden confirmar que son correctas o indicarnos la referencia que falta?\n\n"
                 f"Administracion"))
    raise ValueError(kind)


def queue_email(led: Ledger, file_id: str, kind: str, *, thread_id=None, extra=None,
                hold_seconds: int = UNDO_SECONDS, to: str | None = None,
                invoice: dict | None = None) -> dict:
    from desk import llm_contract
    inv = invoice or (state.get(led, file_id) or {}).get("invoice") or {}
    if kind == "query_amount" and (not extra or extra.get("pedido_importe") is None):
        row = state.get(led, file_id)
        det = next((c.get("detail") or {} for c in (row or {}).get("checks", [])
                    if c.get("canonical") == "AMOUNT"), {})
        extra = {**(extra or {}), "pedido_importe": det.get("pedido_importe")}
    subject, body = _tpl(kind, inv, extra)
    body = llm_contract.phrase(led, "email", body, file_id=file_id, thread_id=thread_id)
    recipient = mail.resolve_recipient(to if to is not None else inv.get("vendor_email"))
    release = datetime.now(UTC) + timedelta(seconds=hold_seconds)
    transport = mail.status()["transport"]
    payload = {"template": kind, "to": recipient, "subject": subject, "body": body,
               "transport": transport, "release_at": release.isoformat(timespec="seconds")}
    if recipient is None and transport == "smtp":
        payload["error"] = "missing_recipient"
        payload["held"] = True
    with led.transaction():
        seq = led.record("EMAIL_QUEUED", file_id=file_id, actor="agent:comunicador",
                         thread_id=thread_id, **payload)
        led.claim(f"email-queue:{seq}", "EMAIL_QUEUED")
    return {**payload, "seq": seq, "file_id": file_id}


def cancel_email(led: Ledger, seq: int) -> bool:
    """Cancel a queued mail. A dispatch that already started can only be
    cancelled when it ended in a definitive failure -- an uncertain send is
    never 'recalled', it must be checked by hand."""
    with led.transaction():
        ev = next((e for e in led.by_kind("EMAIL_QUEUED") if e["seq"] == seq), None)
        if not ev:
            return False
        if any(e["payload"].get("of") == seq for e in led.by_kind("EMAIL_CANCELLED")):
            return False
        if any(e["payload"].get("of") == seq for e in led.by_kind("EMAIL_SENT")):
            return False
        started = any(e["payload"].get("of") == seq
                      for e in led.by_kind("EMAIL_DISPATCH_STARTED"))
        failed = any(e["payload"].get("of") == seq
                     for e in led.by_kind("EMAIL_FAILED"))
        if started and not failed:
            return False
        led.record("EMAIL_CANCELLED", file_id=ev["file_id"], actor="human:alberto",
                   of=seq, template=ev["payload"].get("template"),
                   to=ev["payload"].get("to"),
                   file_ids=ev["payload"].get("file_ids"))
        return True


def outbox(led: Ledger) -> list[dict]:
    cancelled = {e["payload"].get("of") for e in led.by_kind("EMAIL_CANCELLED")}
    sent = {e["payload"].get("of") for e in led.by_kind("EMAIL_SENT")}
    failed = {e["payload"].get("of"): e["payload"].get("reason")
              for e in led.by_kind("EMAIL_FAILED")}
    uncertain = {e["payload"].get("of") for e in led.by_kind("EMAIL_UNCERTAIN")}
    started = {e["payload"].get("of") for e in led.by_kind("EMAIL_DISPATCH_STARTED")}
    out = []
    for e in led.by_kind("EMAIL_QUEUED"):
        if e["seq"] in cancelled or e["seq"] in sent:
            continue
        entry = {**e["payload"], "seq": e["seq"], "file_id": e["file_id"], "ts": e["ts"]}
        if e["seq"] in uncertain or (e["seq"] in started and e["seq"] not in failed):
            entry["delivery"] = "uncertain"
        elif e["seq"] in failed:
            entry["delivery"] = "failed"
            entry["error"] = failed[e["seq"]]
        elif e["payload"].get("held"):
            entry["delivery"] = "held"
        else:
            entry["delivery"] = "queued"
        out.append(entry)
    return out


def _claim_dispatch(led: Ledger, seq: int) -> dict | None:
    """Commit the dispatch claim + EMAIL_DISPATCH_STARTED BEFORE any network
    call: a crash after this point projects 'uncertain', never a silent resend.
    Re-reads the queued event inside the transaction so a cancel that landed
    between the outbox snapshot and here wins."""
    with led.transaction() as con:
        if led.claimed(f"email:{seq}"):
            return None
        ev = next((e for e in led.by_kind("EMAIL_QUEUED") if e["seq"] == seq), None)
        if not ev:
            return None
        p = ev["payload"]
        if any(e["payload"].get("of") == seq
               for e in led.by_kind("EMAIL_CANCELLED", "EMAIL_SENT",
                                    "EMAIL_UNCERTAIN")):
            return None
        if any(e["payload"].get("of") == seq for e in led.by_kind("EMAIL_FAILED")):
            return None
        if p.get("error") == "missing_recipient" \
                or datetime.fromisoformat(p["release_at"]) > datetime.now(UTC):
            return None
        if not led.claim(f"email:{seq}", "EMAIL_DISPATCH_STARTED"):
            return None
        led._insert(con, "EMAIL_DISPATCH_STARTED", file_id=ev["file_id"],
                    actor="agent:comunicador", of=seq, to=p.get("to"),
                    subject=p.get("subject"), template=p.get("template"),
                    transport=p.get("transport"),
                    file_ids=p.get("file_ids"))
        return ev


def flush_outbox(led: Ledger, *, force=False) -> int:
    """Release held emails whose window expired. Idempotent per queued event.

    `force` is accepted for call compatibility but never bypasses the hold:
    a mail only leaves once its release_at has passed. Dispatch is claimed and
    committed before SMTP runs, because an external send cannot be rolled back.
    """
    now = datetime.now(UTC)
    n = 0
    for mail_ in outbox(led):
        if mail_["delivery"] in ("uncertain", "failed"):
            continue
        if mail_.get("error") == "missing_recipient":
            if datetime.fromisoformat(mail_["release_at"]) > now:
                continue
            with led.transaction():
                if led.claim(f"email:{mail_['seq']}", "EMAIL_FAILED"):
                    led.record("EMAIL_FAILED", file_id=mail_["file_id"],
                               actor="agent:comunicador", of=mail_["seq"],
                               to=mail_.get("to"), subject=mail_.get("subject"),
                               template=mail_["template"],
                               file_ids=mail_.get("file_ids"),
                               reason="missing_recipient")
            continue
        ev = _claim_dispatch(led, mail_["seq"])
        if ev is None:
            continue
        res = mail.send({**mail_, "seq": mail_["seq"]}, ledger_id=str(led.path))
        with led.transaction():
            if res["ok"]:
                seq = led.record("EMAIL_SENT", file_id=mail_["file_id"],
                                 actor="agent:comunicador", of=mail_["seq"],
                                 to=mail_.get("to"), subject=mail_["subject"],
                                 template=mail_["template"], transport=res["transport"],
                                 file_ids=mail_.get("file_ids"),
                                 message_id=res.get("message_id"))
                led.link_claim(f"email:{mail_['seq']}", seq)
                n += 1
            elif res.get("uncertain"):
                led.record("EMAIL_UNCERTAIN", file_id=mail_["file_id"],
                           actor="agent:comunicador", of=mail_["seq"],
                           to=mail_.get("to"), subject=mail_["subject"],
                           template=mail_["template"],
                           file_ids=mail_.get("file_ids"), reason=res["code"])
            else:
                led.record("EMAIL_FAILED", file_id=mail_["file_id"],
                           actor="agent:comunicador", of=mail_["seq"],
                           to=mail_.get("to"), subject=mail_["subject"],
                           template=mail_["template"],
                           file_ids=mail_.get("file_ids"), reason=res["code"])
    return n


# --------------------------------------------------------------------------- #
# TESORERO -- approval, exactly-once payment, remesa, notices
# --------------------------------------------------------------------------- #
def idempotency_key(file_id: str, ruleset_version: str, approval_id: str) -> str:
    import hashlib
    raw = f"{file_id}|{ruleset_version}|{approval_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def approve(led: Ledger, file_ids: list[str], *, actor="human:alberto", approval_id=None,
            thread_id=None, note=None, override=False) -> dict:
    """Approve and pay. Replaying this call pays nothing twice -- by construction."""
    approval_id = approval_id or f"AP-{uuid.uuid4().hex[:12]}"
    today = _today()
    remesa = f"REM-{today:%Y%m%d}"
    paid, blocked, refused, total = [], [], [], Decimal(0)
    reasons: dict[str, str | None] = {}

    for file_id in file_ids:
        row = state.get(led, file_id)
        res = led.register_payment(
            file_id, (row or {}).get("ruleset_version") or "-", approval_id, actor,
            thread_id=thread_id, note=note, remesa=remesa,
            value_date=today.isoformat())
        if res["status"] == "paid":
            paid.append(file_id)
            total += Decimal(str(res["amount"]))
            pay = next(e for e in led.by_kind("PAYMENT_REGISTERED")
                       if e["seq"] == res["seq"])
            _queue_payment_notice(led, pay, thread_id=thread_id)
        elif res["status"] == "blocked":
            blocked.append(file_id)
        else:
            refused.append(file_id)
            reasons[file_id] = res.get("reason")

    return {"approval_id": approval_id, "remesa": remesa, "paid": paid,
            "blocked": blocked, "refused": refused,
            "refused_reasons": reasons, "total": float(total)}


def _queue_payment_notice(led: Ledger, pay: dict, *, thread_id=None) -> dict | None:
    """Queue the payment notice for one committed PAYMENT_REGISTERED.

    Drafting (template + optional phrasing) happens OUTSIDE the transaction;
    the claim and the queue event commit together so a crash can never leave
    a claimed-but-missing notice. Everything the mail says comes from the
    immutable creditor snapshot in the payment event, not the live row.
    """
    key = f"notice:{pay['seq']}"
    if led.claimed(key):
        return None
    p = pay["payload"]
    cred = p.get("creditor") or {}
    inv = {"invoice_number": cred.get("invoice_number") or "-",
           "vendor": cred.get("vendor") or "Proveedor",
           "vendor_email": cred.get("vendor_email"),
           "total": p.get("amount"), "pedido": cred.get("pedido")}
    subject, body = _tpl("payment_notice", inv,
                         {"remesa": p.get("remesa"),
                          "value_date": p.get("value_date")})
    from desk import llm_contract
    body = llm_contract.phrase(led, "email", body, file_id=pay["file_id"],
                               thread_id=thread_id)
    recipient = mail.resolve_recipient(cred.get("vendor_email"))
    with led.transaction():
        if not led.claim(key, "EMAIL_QUEUED"):
            return None
        release = datetime.now(UTC) + timedelta(seconds=UNDO_SECONDS)
        seq = led.record("EMAIL_QUEUED", file_id=pay["file_id"],
                         actor="agent:comunicador", thread_id=thread_id,
                         template="payment_notice", to=recipient,
                         subject=subject, body=body,
                         transport=mail.status()["transport"],
                         release_at=release.isoformat(timespec="seconds"),
                         payment_seq=pay["seq"])
        led.link_claim(key, seq)
    return {"seq": seq, "to": recipient}


def recover_notices(led: Ledger) -> int:
    """After a crash, payments committed without their notice get one now.

    The notice claim lives in the same transaction as the queue event, so a
    notice is either fully queued or fully absent -- never half-written.
    """
    n = 0
    for pay in led.by_kind("PAYMENT_REGISTERED"):
        if _queue_payment_notice(led, pay):
            n += 1
    return n


def reject(led: Ledger, file_ids: list[str], *, actor="human:alberto", thread_id=None,
           note=None) -> dict:
    for file_id in file_ids:
        led.record("HUMAN_REJECTED", file_id=file_id, actor=actor, thread_id=thread_id, note=note)
    return {"rejected": file_ids}


PAUSE_CAUSE = {"DUPLICATES": "posible duplicado", "DATES": "fecha fuera de plazo",
               "VENDOR": "cuenta bancaria por confirmar", "AMOUNT": "importe por aclarar",
               "MISSING": "datos incompletos"}


def notify_blocked(led: Ledger, *, thread_id=None) -> list[dict]:
    """One pause notice per (blocking rule, vendor) covering only the files
    nobody has been asked about yet. Held, batched, never a flat rejection --
    the message asks, it does not accuse. The claim key binds the sorted
    file_ids, so a new invoice in the same group gets its own batch."""
    import hashlib

    def _notified():
        cancelled = {e["payload"].get("of") for e in led.by_kind("EMAIL_CANCELLED")}
        seen: set[str] = set()
        for e in led.by_kind("EMAIL_QUEUED"):
            if e["payload"].get("template") == "pause_review" \
                    and e["seq"] not in cancelled:
                seen.update(e["payload"].get("file_ids") or [])
        return seen

    notified = _notified()
    groups: dict[tuple, list[dict]] = {}
    for row in state.invoices(led):
        if row["status"] != "blocked" or row["file_id"] in notified:
            continue
        rule = row["blocking"][0]["canonical"] if row["blocking"] else "OTRO"
        groups.setdefault((rule, row["invoice"]["vendor_id"]), []).append(row)

    out = []
    for (rule, vendor_id), rows in sorted(groups.items()):
        ids = sorted(r["file_id"] for r in rows)
        digest = hashlib.sha256("|".join(ids).encode()).hexdigest()
        first = rows[0]["invoice"]
        items = [{"invoice_number": r["invoice"]["invoice_number"],
                  "total": r["invoice"]["total"]} for r in rows]
        subject, body = _tpl("pause_review", first,
                             {"cause": PAUSE_CAUSE.get(rule, "una revision"),
                              "items": items})
        from desk import llm_contract
        body = llm_contract.phrase(led, "email", body, thread_id=thread_id)
        recipient = mail.resolve_recipient(first.get("vendor_email"))
        with led.transaction():
            queued_by_seq = {e["seq"]: e for e in led.by_kind("EMAIL_QUEUED")}
            cancelled_here = [
                e["payload"]["of"] for e in led.by_kind("EMAIL_CANCELLED")
                if (queued_by_seq.get(e["payload"].get("of")) or {})
                .get("payload", {}).get("vendor_id") == vendor_id
                and queued_by_seq[e["payload"]["of"]]["payload"].get("cause") == rule]
            generation = max(cancelled_here) if cancelled_here else "initial"
            key = f"pause:{digest}:{generation}"
            fresh = _notified()
            if any(f in fresh for f in ids):
                continue
            if not led.claim(key, "EMAIL_QUEUED"):
                continue
            release = datetime.now(UTC) + timedelta(seconds=UNDO_SECONDS)
            seq = led.record("EMAIL_QUEUED", file_id=None, actor="agent:comunicador",
                             thread_id=thread_id, template="pause_review",
                             to=recipient, subject=subject, body=body,
                             transport=mail.status()["transport"],
                             file_ids=ids,
                             vendor_id=vendor_id, cause=rule,
                             release_at=release.isoformat(timespec="seconds"))
            led.link_claim(key, seq)
        out.append({"seq": seq, "to": recipient, "subject": subject,
                    "file_ids": ids, "cause": rule})
    return out


def remesa_xml(led: Ledger, remesa_id: str | None = None) -> tuple[str, str]:
    """SEPA pain.001.001.03 for the registered payments -- the real artifact.

    Built ONLY from the immutable creditor snapshot stored in each
    PAYMENT_REGISTERED event, so a reprocess can never rewrite a remesa.
    """
    all_pays = led.by_kind("PAYMENT_REGISTERED")
    if remesa_id is None:
        remesa_id = (all_pays[-1]["payload"]["remesa"] if all_pays
                     else f"REM-{_today():%Y%m%d}")
    pays = [p for p in all_pays if p["payload"].get("remesa") == remesa_id]
    today = (pays[0]["payload"].get("value_date") if pays
             else None) or _today().isoformat()
    now = datetime.now(UTC).isoformat(timespec="seconds")

    NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"
    doc = ET.Element("Document", xmlns=NS)
    root = ET.SubElement(doc, "CstmrCdtTrfInitn")

    amounts = [Decimal(str(p["payload"]["amount"])).quantize(Decimal("0.01"))
               for p in pays]
    total = sum(amounts, Decimal(0))

    hdr = ET.SubElement(root, "GrpHdr")
    ET.SubElement(hdr, "MsgId").text = remesa_id
    ET.SubElement(hdr, "CreDtTm").text = now
    ET.SubElement(hdr, "NbOfTxs").text = str(len(pays))
    ET.SubElement(hdr, "CtrlSum").text = str(total)
    ET.SubElement(hdr, "InitgPty").append(_nm("Alberto - Administracion"))

    inf = ET.SubElement(root, "PmtInf")
    ET.SubElement(inf, "PmtInfId").text = remesa_id
    ET.SubElement(inf, "PmtMtd").text = "TRF"
    ET.SubElement(inf, "NbOfTxs").text = str(len(pays))
    ET.SubElement(inf, "CtrlSum").text = str(total)
    ET.SubElement(inf, "ReqdExctnDt").text = today
    dbtr = ET.SubElement(inf, "Dbtr")
    ET.SubElement(dbtr, "Nm").text = "Alberto S.L."
    acct = ET.SubElement(ET.SubElement(inf, "DbtrAcct"), "Id")
    ET.SubElement(acct, "IBAN").text = "ES9121000418450200051332"
    agent = ET.SubElement(ET.SubElement(inf, "DbtrAgt"), "FinInstnId")
    ET.SubElement(agent, "BIC").text = "CAIXESBBXXX"
    ET.SubElement(inf, "ChrgBr").text = "SLEV"

    for p, amount in zip(pays, amounts):
        pl = p["payload"]
        cred = pl.get("creditor") or {}
        tx = ET.SubElement(inf, "CdtTrfTxInf")
        pmt = ET.SubElement(tx, "PmtId")
        ET.SubElement(pmt, "EndToEndId").text = str(
            cred.get("invoice_number") or p["file_id"])[:35]
        amt = ET.SubElement(ET.SubElement(tx, "Amt"), "InstdAmt", Ccy="EUR")
        amt.text = str(amount)
        ET.SubElement(ET.SubElement(tx, "Cdtr"), "Nm").text = str(
            cred.get("vendor") or "")
        cacct = ET.SubElement(ET.SubElement(tx, "CdtrAcct"), "Id")
        ET.SubElement(cacct, "IBAN").text = str(cred.get("iban") or pl.get("iban") or "")
        rmt = ET.SubElement(tx, "RmtInf")
        ET.SubElement(rmt, "Ustrd").text = (
            f"Factura {cred.get('invoice_number')} / pedido {cred.get('pedido') or 'n/d'}")

    return remesa_id, ET.tostring(doc, encoding="unicode", xml_declaration=True)


def _nm(name: str) -> ET.Element:
    el = ET.Element("Nm")
    el.text = name
    return el


# --------------------------------------------------------------------------- #
# REPORTERO -- the end-of-day report Alberto forwards upward
# --------------------------------------------------------------------------- #
def daily_report(led: Ledger) -> dict:
    rows = state.invoices(led)
    s = state.summary(led, rows)
    today = _today()
    today_events = [e for e in led.all_events() if e["ts"][:10] == today.isoformat()]
    pays = [e for e in today_events if e["kind"] == "PAYMENT_REGISTERED"]
    paid_count = len(pays)
    paid_total = float(sum(Decimal(str(e["payload"]["amount"])) for e in pays))
    processed_today = len({e["file_id"] for e in today_events
                           if e["kind"] == "VERDICT"})
    cost_today = sum(e["cost_usd"] or 0 for e in today_events)
    unknown_today = sum(1 for e in today_events
                        if e["payload"].get("cost_known") is False)
    touched = len({e["file_id"] for e in today_events
                   if e["kind"] in ("HUMAN_APPROVED", "HUMAN_REJECTED")})

    by_vendor: dict[str, dict] = {}
    for e in pays:
        vendor = (e["payload"].get("creditor") or {}).get("vendor") or "Proveedor"
        v = by_vendor.setdefault(vendor, {"n": 0, "total": 0.0})
        v["n"] += 1
        v["total"] += float(Decimal(str(e["payload"]["amount"])))

    blocked = [r for r in rows if r["status"] == "blocked"]
    causes: dict[str, int] = {}
    for r in blocked:
        c = r["blocking"][0]["canonical"] if r["blocking"] else "OTRO"
        causes[c] = causes.get(c, 0) + 1

    lines = [
        f"CIERRE DEL {today:%d/%m/%Y} - {processed_today} facturas procesadas hoy",
        "",
        f"Pagos preparados hoy ..... {paid_count:>4}   {money(paid_total)}",
        f"En cola (pendiente actual)  {s['queued']:>4}   {money(s['queued_total'])}",
        f"Retenidas (NO PAGAR) ..... {s['blocked']:>4}   " +
        ", ".join(f"{k.lower()} x{v}" for k, v in causes.items()),
        f"Escaladas (pendiente actual) {s['escalated']:>4}   en {s['clusters']} patrones",
        f"Investigadas, sin cerrar . {s['auto_resolved']:>4}",
        f"Tocadas por Alberto hoy .. {touched:>4}",
        "",
        f"Ahorro por duplicados detectados: {money(s['duplicates_saved'])}",
        f"Coste de hoy: {cost_today:.2f} USD"
        + (f"  ({unknown_today} evento(s) sin coste conocido)" if unknown_today else ""),
        f"Reintentos absorbidos del ERP: {s['erp_retries']}   Caidas de proveedor cubiertas: {s['fallbacks']}",
        f"Ruleset vigente: {s['ruleset_version']}",
        "",
        "Pagos de hoy por proveedor:",
    ]
    for v, agg in sorted(by_vendor.items(), key=lambda kv: -kv[1]["total"]):
        lines.append(f"  {v:<38} {agg['n']:>3} facturas   {money(agg['total'])}")
    lines += ["", "Traza completa de cada decision disponible en el expediente de cada factura."]
    return {"subject": f"Cierre de facturacion {today:%d/%m/%Y}",
            "body": "\n".join(lines), "summary": s, "paid_total": paid_total}


def send_report(led: Ledger, to: str | None = None, *, thread_id=None) -> dict:
    """Queue the daily digest: ONE per day+recipient, held like any email."""
    rep = daily_report(led)
    to = mail.resolve_recipient(to or mail.status().get("report_to"))
    if not to:
        return {**rep, "queued": False, "error": "missing_recipient"}
    key = f"report:{_today().isoformat()}:{to}"
    with led.transaction():
        if not led.claim(key, "EMAIL_QUEUED"):
            return {**rep, "queued": False, "duplicate": True, "to": to}
        release = datetime.now(UTC) + timedelta(seconds=UNDO_SECONDS)
        seq = led.record("EMAIL_QUEUED", actor="agent:reportero", thread_id=thread_id,
                         template="daily_report", report_kind="daily_report",
                         to=to, subject=rep["subject"], body=rep["body"],
                         transport=mail.status()["transport"],
                         metrics=rep["summary"],
                         release_at=release.isoformat(timespec="seconds"))
        led.link_claim(key, seq)
    return {**rep, "queued": True, "duplicate": False, "to": to, "seq": seq}
