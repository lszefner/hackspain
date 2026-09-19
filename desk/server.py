"""Local HTTP server for the desk. Stdlib only -- same spirit as alberto_erp.py.

    python3 desk/server.py                 # live ledger, http://127.0.0.1:8011
    python3 desk/server.py --demo          # seeded demo ledger, no network
    python3 desk/server.py --db path.db --limit 20 --investigate
"""
from __future__ import annotations

import sys

# This repo targets >=3.11; the system python3 may be older. Re-exec into the
# project venv before importing anything that needs it.
if sys.version_info < (3, 11):  # noqa: UP036
    import os
    from pathlib import Path as _P
    _venv_py = _P(__file__).resolve().parent.parent / ".venv" / "bin" / "python"
    if _venv_py.is_file() and os.environ.get("DESK_REEXEC") != "1":
        os.environ["DESK_REEXEC"] = "1"
        os.execv(str(_venv_py), [str(_venv_py), __file__, *sys.argv[1:]])
    print("desk necesita Python >= 3.11. Crea el entorno con "
          "`uv sync --locked --extra worker` y arranca con "
          "`uv run --locked --extra worker python desk/server.py`.")
    raise SystemExit(1)

import argparse
import json
import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desk import agents, chat, llm, mail, rules, state
from desk.ledger import Ledger

HERE = Path(__file__).resolve().parent
# The interface lives in desk/mock/ now and runs its own server. This process
# is the engine and the API; it no longer serves a page of its own.
UI_MOVED = (b"The desk API is up. The interface lives in desk/mock/ -- "
            b"run `python3 desk/mock/serve.py` and open the port it prints.\n")

LED: Ledger | None = None
DEMO = False
MAX_BODY = 64 * 1024


def _default(o):
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def jdump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=_default).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "AlbertoDesk/1.0"

    def log_message(self, *a):
        pass

    def _send(self, status: int, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, jdump(obj))

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("invalid_length") from None
        if n < 0 or n > MAX_BODY:
            raise ValueError("body_too_large")
        return json.loads(self.rfile.read(n) or b"{}")

    # -- GET ---------------------------------------------------------------
    def do_GET(self):
        try:
            return self._get()
        except Exception as exc:  # noqa: BLE001 - never leak a traceback over HTTP
            return self._json({"error": type(exc).__name__}, 500)

    def _get(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._send(200, UI_MOVED, "text/plain; charset=utf-8")
        if p == "/favicon.ico":
            return self._send(204, b"")

        if p == "/api/brief":
            return self._json(chat.brief(LED, demo=DEMO))

        if p == "/api/invoices":
            rows = state.invoices(LED)
            status = (q.get("status") or [""])[0]
            term = chat.fold((q.get("q") or [""])[0])
            if status:
                rows = [r for r in rows if r["status"] == status]
            if term:
                rows = [r for r in rows if term in chat.fold(
                    r["file_id"] + str(r["invoice"].get("vendor") or "")
                    + str(r["invoice"].get("invoice_number") or ""))]
            return self._json({"rows": chat._table(rows, 500)["rows"],
                               "summary": state.summary(LED)})

        if p == "/api/trace":
            fid = (q.get("file_id") or [""])[0]
            tb = chat.trace_block(LED, fid)
            return self._json(tb or {"error": "not found"}, 200 if tb else 404)

        if p == "/api/payments":
            rows = state.invoices(LED)
            paid = [r for r in rows if r["status"] == "paid"]
            queued = [r for r in rows if r["status"] == "queued"]
            remesa_id, _ = agents.remesa_xml(LED)
            return self._json({
                "summary": state.summary(LED, rows),
                "remesa_id": remesa_id,
                "paid": chat._table(paid, 500)["rows"],
                "queued": chat._table(queued, 500)["rows"],
                "outbox": agents.outbox(LED),
                "sent": [{"to": e["payload"].get("to"), "subject": e["payload"].get("subject"),
                          "file_id": e["file_id"], "ts": e["ts"],
                          "transport": e["payload"].get("transport")}
                         for e in LED.by_kind("EMAIL_SENT")][-40:],
                "failed": [{"to": e["payload"].get("to"), "subject": e["payload"].get("subject"),
                            "file_id": e["file_id"], "ts": e["ts"],
                            "reason": e["payload"].get("reason")}
                           for e in LED.by_kind("EMAIL_FAILED", "EMAIL_UNCERTAIN")][-40:],
                "blocked_payments": [{"file_id": e["file_id"], **e["payload"]}
                                     for e in LED.by_kind("PAYMENT_BLOCKED")],
                "report": {**agents.daily_report(LED), "to": mail.status()["report_to"]
                           or mail.status()["recipient_override"]},
            })

        if p == "/api/activity":
            try:
                limit = int((q.get("limit") or ["150"])[0])
            except (TypeError, ValueError):
                return self._json({"error": "invalid_limit"}, 400)
            if not (0 < limit <= 5000):
                return self._json({"error": "invalid_limit"}, 400)
            return self._json({"events": LED.recent(limit), "totals": LED.totals()})

        if p == "/api/rules":
            applied = [{"ts": e["ts"], **e["payload"]} for e in LED.by_kind("RULE_APPLIED")]
            return self._json({"ruleset": rules.current(LED), "history": applied})

        if p == "/api/remesa.xml":
            rid, xml = agents.remesa_xml(LED, (q.get("id") or [None])[0])
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{rid}.xml"')
            data = xml.encode("utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        return self._send(404, b'{"error":"not found"}')

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        try:
            return self._post()
        except Exception as exc:  # noqa: BLE001 - never leak a traceback over HTTP
            return self._json({"error": type(exc).__name__}, 500)

    def _post(self):
        p = urlparse(self.path).path
        origin = self.headers.get("Origin")
        if origin is not None \
                and origin != f"http://{self.headers.get('Host')}":
            return self._json({"error": "origin_rejected"}, 403)
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() \
                != "application/json":
            return self._json({"error": "expected application/json"}, 400)
        try:
            body = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            code = "body_too_large" if "too_large" in str(exc) else "invalid_json"
            return self._json({"error": code}, 400 if code == "invalid_json" else 413)
        if not isinstance(body, dict):
            return self._json({"error": "invalid_body"}, 400)

        if p == "/api/chat":
            text = body.get("text", "")
            if not isinstance(text, str) or len(text) > 8000:
                return self._json({"error": "invalid_text"}, 400)
            return self._json(chat.respond(LED, text,
                                           thread_id=body.get("thread_id", "main")))

        if p == "/api/action":
            res = self._action(body)
            if isinstance(res, tuple):
                return self._json(res[0], res[1])
            return self._json(res)

        return self._send(404, b'{"error":"not found"}')

    def _known(self, ids: list) -> list | None:
        bad = [f for f in ids if not isinstance(f, str) or state.get(LED, f) is None]
        return bad or None

    def _action(self, b: dict) -> dict:
        act = b.get("action")
        ids = b.get("file_ids") or ([b["file_id"]] if b.get("file_id") else [])
        if not isinstance(ids, list) or len(ids) > 500:
            return {"ok": False, "message": "file_ids invalido"}, 400
        if act in ("approve", "reject", "email"):
            bad = self._known(ids)
            if bad:
                return ({"ok": False,
                         "message": "facturas desconocidas: "
                                    + ", ".join(map(str, bad))}, 400)
        if "approval_id" in b and b["approval_id"] is not None \
                and not isinstance(b["approval_id"], str):
            return {"ok": False, "message": "approval_id invalido"}, 400
        if "reprocess" in b and not isinstance(b["reprocess"], bool):
            return {"ok": False, "message": "reprocess invalido"}, 400

        if act == "approve":
            res = agents.approve(LED, ids, note=b.get("note"),
                                 approval_id=b.get("approval_id"))
            msg = ""
            if res["paid"]:
                msg = (f"Aprobadas y registradas {len(res['paid'])} facturas por "
                       f"{agents.money(res['total'])} en la remesa {res['remesa']}. "
                       f"Aviso de pago en cola para cada proveedor.")
            if res.get("refused"):
                reasons = "; ".join(
                    f"{f}: {res.get('refused_reasons', {}).get(f) or 'rechazada'}"
                    for f in res["refused"])
                msg += (" " if msg else "") + f"{len(res['refused'])} no las pago ({reasons}). " \
                    f"Si el veredicto esta mal, lo que hay que cambiar es la regla, " \
                    f"y eso pasa por diff y backtest."
            if res["blocked"]:
                msg += ((" " if msg else "") +
                        f"{len(res['blocked'])} no se han pagado otra vez: ya tenian pago "
                        f"registrado, asi que el tesorero lo ha bloqueado. Queda anotado "
                        f"como PAYMENT_BLOCKED en la traza, no en silencio.")
            return {"ok": True, "message": msg, "result": res}

        if act == "reject":
            agents.reject(LED, ids, note=b.get("note"))
            return {"ok": True, "message": f"{len(ids)} factura(s) rechazadas y registradas."}

        if act == "investigate":
            n = agents.investigate_all(LED)
            return {"ok": True, "message": f"He investigado {n} escalacion(es) nuevas."}

        if act == "email":
            kind = b.get("template", "request_po")
            try:
                sent = [agents.queue_email(LED, f, kind) for f in ids]
            except ValueError:
                return {"ok": False, "message": f"plantilla desconocida: {kind}"}, 400
            return {"ok": True, "message": (f"{len(sent)} correo(s) en la bandeja de salida, "
                                            f"retenidos {agents.UNDO_SECONDS}s por si te arrepientes."),
                    "emails": sent}

        if act == "notify_blocked":
            out = agents.notify_blocked(LED)
            return {"ok": True, "message": (f"{len(out)} avisos de pausa en cola, uno por causa "
                                            f"y proveedor. Ninguno dice 'rechazada': preguntan.")}

        if act == "cancel_email":
            try:
                seq = int(b["seq"])
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "message": "seq invalido"}, 400
            ok = agents.cancel_email(LED, seq)
            return {"ok": ok, "message": ("Correo cancelado." if ok else
                                          "Ya habia salido o estaba en envio; no se puede "
                                          "deshacer.")}

        if act == "flush_outbox":
            n = agents.flush_outbox(LED)
            return {"ok": True, "message": (f"{n} correo(s) liberados (solo los que ya cumplieron "
                                            f"su ventana; la retencion no se fuerza).")}

        if act == "apply_rule":
            prop = b.get("proposal")
            if not isinstance(prop, dict) or not prop.get("seq"):
                return {"ok": False, "message": "propuesta invalida: falta seq"}, 400
            try:
                res = rules.apply(LED, prop, reprocess=bool(b.get("reprocess", True)))
            except ValueError as exc:
                return {"ok": False, "message": str(exc)}, 409
            return {"ok": True, "result": res,
                    "message": (f"Ruleset {res['version']} aplicado. "
                                + (f"Reprocesadas {res['reprocessed']} facturas; "
                                   f"{len(res['changed'])} cambian de veredicto: su veredicto "
                                   f"anterior sigue en la traza, no se ha sobrescrito."
                                   if res["changed"] else "Ninguna factura cambia de veredicto."))}

        if act == "send_report":
            rep = agents.send_report(LED, b.get("to"))
            if rep.get("error") == "missing_recipient":
                return {"ok": False,
                        "message": "No hay destinatario configurado (DESK_REPORT_TO)."}
            return {"ok": True, "message": (f"Cierre retenido en la bandeja para {rep['to']}; "
                                            f"sale al vencer la ventana."
                                            if rep["queued"]
                                            else f"Ya habia un cierre en cola para {rep['to']} "
                                                 f"hoy; no lo duplico.")}

        if act == "reseed":
            if not DEMO:
                return ({"ok": False,
                         "message": "El dataset en vivo no se regenera desde la UI."}, 403)
            new_path = HERE / "data" / f"demo-{datetime.now(UTC):%Y%m%d%H%M%S%f}.db"
            from desk import seed
            globals()["LED"] = Ledger(new_path)
            seed.build(globals()["LED"])
            return {"ok": True, "message": f"Nueva demo en {new_path.name}."}

        return {"ok": False, "message": f"accion desconocida: {act}"}


def _outbox_worker() -> None:
    while True:
        led = LED
        if led is not None:
            try:
                agents.flush_outbox(led)
                agents.recover_notices(led)
            except Exception as exc:  # noqa: BLE001
                print(f"[desk] outbox worker: {exc}", file=sys.stderr)
        time.sleep(1)


def _ingest_worker(led: Ledger, limit) -> None:
    try:
        from desk import ingest
        result = ingest.run(led, limit=limit)
        if isinstance(result, dict) and result.get("error"):
            return
        agents.investigate_all(led)
    except Exception as exc:  # noqa: BLE001
        print(f"[desk] ingest worker: {exc}", file=sys.stderr)


def main() -> int:
    global LED, DEMO
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--reseed", action="store_true",
                    help="solo con --demo: crea un ledger demo nuevo (nunca borra)")
    ap.add_argument("--limit", type=int, default=None,
                    help="acota la ingesta a N ficheros")
    ap.add_argument("--investigate", action="store_true",
                    help="run the investigator over open escalations at boot")
    args = ap.parse_args()

    DEMO = args.demo
    if args.reseed and not DEMO:
        ap.error("--reseed solo existe en modo --demo")

    if DEMO:
        db = args.db or (HERE / "data" /
                         (f"demo-{datetime.now(UTC):%Y%m%d%H%M%S%f}.db" if args.reseed
                          else "demo.db"))
        llm.configure(False)
        mail.configure(False)
    else:
        db = args.db or HERE / "data" / "live.db"
    LED = Ledger(db)

    if DEMO and LED.totals()["events"] == 0:
        from desk import seed
        seed.build(LED)
        print("dataset demo generado")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)

    threading.Thread(target=_outbox_worker, daemon=True).start()
    if not DEMO:
        threading.Thread(target=_ingest_worker, args=(LED, args.limit),
                         daemon=True).start()
    if args.investigate:
        threading.Thread(target=agents.investigate_all, args=(LED,),
                         daemon=True).start()

    print(f"desk en http://127.0.0.1:{args.port} ({'demo' if DEMO else 'live'} · {db.name})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
