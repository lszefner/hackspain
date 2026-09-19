"""Outbound mail for the desk. Real SMTP over stdlib, or an explicit sandbox.

Transport is decided by configuration, never by silence: without DESK_SMTP_*
the transport is 'smtp-sandbox' and every send is reported as such, visibly.
Recipient override (DESK_EMAIL_TO) is applied at QUEUE time by the caller, so
what the ledger holds is what would really leave. No credential ever lands in
an event payload, a status dict or an error message.
"""
from __future__ import annotations

import hashlib
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formatdate

TIMEOUT = 10

_lock = threading.Lock()
_enabled = True


def configure(enabled: bool = True) -> None:
    """--demo switches the whole transport off: zero network, always sandbox."""
    global _enabled
    with _lock:
        _enabled = enabled


def _env() -> dict:
    try:
        port = int(os.environ.get("DESK_SMTP_PORT", "587") or 587)
    except ValueError:
        port = None
    return {
        "host": os.environ.get("DESK_SMTP_HOST", ""),
        "port": port,
        "username": os.environ.get("DESK_SMTP_USERNAME", ""),
        "password": os.environ.get("DESK_SMTP_PASSWORD", ""),
        "from": os.environ.get("DESK_SMTP_FROM", ""),
        "starttls": os.environ.get("DESK_SMTP_STARTTLS", "true").lower() != "false",
        "ssl": os.environ.get("DESK_SMTP_SSL", "false").lower() == "true",
        "to_override": os.environ.get("DESK_EMAIL_TO", ""),
        "report_to": os.environ.get("DESK_REPORT_TO", ""),
    }


def _config_error(cfg: dict) -> str | None:
    if not cfg["host"] or not cfg["from"]:
        return None
    if cfg["port"] is None or not (0 < cfg["port"] < 65536):
        return "invalid_port"
    if cfg["username"] and not (cfg["ssl"] or cfg["starttls"]):
        return "credentials_require_tls"
    return None


def status() -> dict:
    cfg = _env()
    error = _config_error(cfg)
    configured = bool(cfg["host"] and cfg["from"]) and error is None
    return {"transport": "smtp" if (configured and _enabled) else "smtp-sandbox",
            "configured": configured,
            "error": error,
            "recipient_override": cfg["to_override"],
            "report_to": cfg["report_to"]}


def resolve_recipient(default: str | None) -> str | None:
    """What queue time stores as `to`: the override, else the real address."""
    cfg = _env()
    if cfg["to_override"]:
        return cfg["to_override"]
    return default or None


def message_id(seq: int, ledger_id: str) -> str:
    digest = hashlib.sha256(ledger_id.encode()).hexdigest()[:12]
    return f"<desk-{digest}-{seq}@mesa-de-alberto.local>"


def _bad_headers(*values) -> bool:
    return any(v and ("\r" in str(v) or "\n" in str(v)) for v in values)


def send(payload: dict, *, ledger_id: str = "ledger") -> dict:
    """Deliver one queued payload. Never raises; returns an honest outcome.

    {ok, transport, message_id} | {ok:False, code, uncertain:bool}
    'uncertain' means the failure happened at a point where the server may
    already have accepted the message -- the caller records EMAIL_UNCERTAIN
    and does not autoretry, because a retry could duplicate the send.
    A payload queued as smtp-sandbox stays sandbox even if SMTP is configured
    later: old demo mail never escapes onto the real network.
    """
    if payload.get("transport") == "smtp-sandbox":
        return {"ok": True, "transport": "smtp-sandbox",
                "message_id": message_id(payload.get("seq", 0), ledger_id)}
    cfg = _env()
    error = _config_error(cfg)
    if not (cfg["host"] and cfg["from"] and _enabled and error is None):
        return {"ok": True, "transport": "smtp-sandbox",
                "message_id": message_id(payload.get("seq", 0), ledger_id)} \
            if error is None else \
            {"ok": False, "code": error, "uncertain": False}
    to = payload.get("to")
    if not to:
        return {"ok": False, "code": "missing_recipient", "uncertain": False}
    if _bad_headers(to, payload.get("subject"), cfg["from"]):
        return {"ok": False, "code": "invalid_headers", "uncertain": False}

    try:
        msg = EmailMessage()
        msg["From"] = cfg["from"]
        msg["To"] = to
        msg["Subject"] = payload.get("subject", "")
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = message_id(payload.get("seq", 0), ledger_id)
        msg.set_content(payload.get("body", ""))
        cls = smtplib.SMTP_SSL if cfg["ssl"] else smtplib.SMTP
        with cls(cfg["host"], cfg["port"], timeout=TIMEOUT) as smtp:
            if cfg["starttls"] and not cfg["ssl"]:
                smtp.starttls(context=ssl.create_default_context())
            if cfg["username"]:
                smtp.login(cfg["username"], cfg["password"])
            smtp.send_message(msg)
        return {"ok": True, "transport": "smtp",
                "message_id": msg["Message-ID"]}
    except smtplib.SMTPRecipientsRefused:
        return {"ok": False, "code": "recipients_refused", "uncertain": False}
    except smtplib.SMTPResponseException as exc:
        return {"ok": False, "code": f"smtp_{exc.smtp_code}", "uncertain": False}
    except (ValueError, OSError, TimeoutError, smtplib.SMTPException):
        return {"ok": False, "code": "smtp_unknown_delivery", "uncertain": True}
