"""Mock server for the desk's main screen.

Two jobs: serve the page, and answer the chat. The answer is led and scoped --
the blocks on screen are chosen by a deterministic router and rendered from
state.py, and the model is only allowed to write the sentence in front of them.
It is handed the facts and told it may not invent one. The key stays here and
never reaches the browser.

Needs Python 3.11 or newer, like the rest of the repo, and nothing else --
stdlib only. From the repo root:

    .venv/bin/python desk/mock/serve.py          # http://127.0.0.1:8770
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import state

HERE = Path(__file__).resolve().parent
REPO = Path("/Users/miquel/Desktop/hackspain/hackspain")

BASE_URL = "https://api.helmcode.com/v1"
MODEL = os.environ.get("DESK_MODEL", "deepseek-v4-flash")
TIMEOUT = 45
# Cloudflare in front of the gateway answers 1010 to urllib's default agent.
UA = "curl/8.7.1"


def api_key() -> str:
    key = os.environ.get("HELMCODE_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(("DEEPSEEK_API_KEY=", "HELMCODE_API_KEY=")):
                return line.split("=", 1)[1].strip()
    raise SystemExit("no HELMCODE_API_KEY / DEEPSEEK_API_KEY found")


KEY = api_key()


# --------------------------------------------------------------------------- #
# routing: the blocks are chosen here, never by the model
# --------------------------------------------------------------------------- #
def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


INTENTS = [
    ("payments",   ("pay", "payment", "sepa", "remesa", "transfer", "settle", "remittance")),
    ("queue",      ("review", "judgement", "judgment", "needs me", "need me", "escalat",
                    "attention", "criterio", "decide", "approve", "pending")),
    ("report",     ("report", "inform", "informe", "close", "closing", "summary", "resumen",
                    "brief", "how did it go", "how it went")),
    ("duplicates", ("duplicate", "twice", "double", "repeated")),
    ("rules",      ("rule", "ruleset", "norma", "policy")),
    ("waiting",    ("waiting", "chase", "chasing", "owes us", "outstanding")),
]


_FILE = re.compile(r"([\w./\-\u00c0-\u024f]+\.pdf)", re.IGNORECASE)


def named_file(text: str) -> str | None:
    m = _FILE.search(text or "")
    if not m:
        return None
    name = os.path.basename(m.group(1))
    return name if any(r["file"] == name for r in state.archive()) else None


def route(text: str) -> list[str]:
    """One list on screen, chosen here. Highest keyword count wins; ties go to
    the earlier, more specific intent. The model never picks."""
    t = fold(text)
    scored = [(sum(k in t for k in keys), -i, name)
              for i, (name, keys) in enumerate(INTENTS)]
    best = max(scored)
    return [best[2]] if best[0] else []


SYSTEM = """You are the desk that works Alberto's accounts payable at a company in Valencia.
Alberto is the accounts payable manager. You processed his invoices overnight, on your own.

HOW YOU TALK
- English. Plain, operational, first person, like a colleague handing over a shift.
- Say what you did: "I checked", "I wrote to", "I stopped", "I would pay". Never call yourself
  an agent, an assistant, an AI or a model. Never offer further help. Never greet.
- AT MOST TWO SENTENCES, under 45 words in total. Shorter is better.
- No bullet points, no headings, no markdown, no emoji.
- You are allowed to be funny, but dry and in passing: one small aside at most, the kind a
  sharp colleague makes about a supplier who always pulls the same trick. Never a joke instead
  of a fact, never a joke about money going wrong, and never two jokes in a row.

WHAT TO SAY
- A list is already on screen under your sentence. Do NOT restate it: no totals, no counts,
  no supplier-by-supplier rundown that the list already shows.
- Say the one thing the list cannot: what you would do first and why, or the single risk
  Alberto should not miss. Then stop.

HARD RULES
- Every figure, supplier, invoice id and date must come from the FACTS. If it is not in the
  FACTS you do not have it, and you say so in one sentence.
- Never invent, estimate or re-round a number. Never claim an action you were not given.
- Off-topic questions: say plainly you do not have it, then name the nearest thing you do."""


def build_messages(text: str, blocks: list[str], history: list[dict],
                   named: str | None = None) -> list[dict]:
    world = state.facts()
    if named:
        d = state.dossier(named)
        world = {**world, "the_invoice_he_is_asking_about": {
            "file": named, "verdict": d["row"]["action"], "supplier": d["row"]["vendor"],
            "read_off_the_page": dict(d["fields"]), "rules": d["checks"],
            "already_decided": state.DECIDED.get(named)}}
    facts = json.dumps(world, ensure_ascii=False, indent=None, default=str)
    on_screen = (", ".join(blocks) if blocks else "nothing")
    ctx = (f"FACTS (the only source of truth):\n{facts}\n\n"
           f"ON SCREEN right now, directly under your sentence: {on_screen}.\n"
           f"Do not repeat what that list already shows.")
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "system", "content": ctx}]
    for h in history[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:1500]})
    msgs.append({"role": "user", "content": text[:1500]})
    return msgs


def stream_llm(messages):
    """Yield content deltas, then a final usage dict."""
    body = json.dumps({
        "model": MODEL, "messages": messages, "stream": True,
        "max_tokens": 300, "temperature": 0.3,
        "stream_options": {"include_usage": True},
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 "Accept": "text/event-stream", "User-Agent": UA},
    )
    usage = None
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content")
                if piece:
                    yield piece, None
    yield None, (usage or {})



# --------------------------------------------------------------------------- #
# uploads. The file inspection is real -- names, sizes and what is actually
# inside a zip. The verdicts are seeded from the file name so the demo is
# stable, and every batch panel carries the word demo for that reason.
# --------------------------------------------------------------------------- #
MAX_UPLOAD = 64 * 1024 * 1024


def human(n):
    return f"{n/1048576:.1f} MB" if n >= 1048576 else f"{max(1, n // 1024)} KB"


def elapsed(sec):
    """A real run over a few dozen pages takes milliseconds. Saying 0.0 s makes
    it look broken, so it is said in the unit it actually happened in."""
    return f"{sec:.1f} s" if sec >= 1 else f"{round(sec * 1000)} ms"


def clock(sec):
    return f"{sec // 60} min {sec % 60} s" if sec >= 60 else f"{sec} s"


def inspect(name, blob):
    """What actually came in. Nothing here is invented."""
    low = name.lower()
    if low.endswith(".pdf"):
        if not blob.startswith(b"%PDF"):
            return {"name": name, "size": len(blob), "ok": False,
                    "reason": "not a PDF inside, whatever the name says", "invoices": []}
        return {"name": name, "size": len(blob), "ok": True, "kind": "pdf", "invoices": [name]}

    if low.endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            return {"name": name, "size": len(blob), "ok": False,
                    "reason": "the zip is damaged and will not open", "invoices": []}
        good, bad = [], []
        for e in zf.infolist():
            base = e.filename.rsplit("/", 1)[-1]
            if e.is_dir() or base.startswith(".") or not base:
                continue
            if base.lower().endswith(".pdf"):
                good.append(base)
            else:
                bad.append([base, "not a PDF"])
        return {"name": name, "size": len(blob), "ok": True, "kind": "zip",
                "invoices": good, "inside_rejected": bad}

    return {"name": name, "size": len(blob), "ok": False,
            "reason": f"{low.rsplit('.', 1)[-1] if '.' in low else 'this'} is not a file I read", "invoices": []}


# What came up the wire is held here until the run is over: a zip is no use as
# a verdict, only as the pages inside it. Memory only, capped, oldest dropped
# first -- this is a demo desk, not a filing cabinet.
HELD: dict = {}
RUNS: dict = {}
HELD_MAX = 6


def hold(blob, info):
    """Keep the bytes under a handle the page can send back to us."""
    key = hashlib.sha256(blob[:4096] + str(len(blob)).encode()
                         + info["name"].encode()).hexdigest()[:12]
    HELD[key] = {"blob": blob, "info": info}
    for stale in list(HELD)[:-HELD_MAX]:
        HELD.pop(stale, None)
    return key


def pages(key):
    """Every PDF inside what was held, in the order the archive lists them."""
    held = HELD.get(key)
    if not held:
        return []
    info, blob = held["info"], held["blob"]
    if info.get("kind") == "pdf":
        return [(info["name"], blob)]
    out = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return []
    for e in sorted(zf.infolist(), key=lambda e: e.filename):
        base = e.filename.rsplit("/", 1)[-1]
        if e.is_dir() or base.startswith(".") or not base.lower().endswith(".pdf"):
            continue
        try:
            out.append((base, zf.read(e)))
        except Exception:                                          # noqa: BLE001, S112
            continue
    return out


def seeded(name, lo, hi):
    h = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    return lo + h % (hi - lo + 1)


def outcome(files):
    """Fold the inspected files into one batch result."""
    invoices, rejected, size = [], [], 0
    for f in files:
        size += f.get("size", 0)
        if not f.get("ok"):
            rejected.append([f["name"], f.get("reason", "could not read it")])
            continue
        rejected.extend(f.get("inside_rejected", []))
        invoices.extend(f.get("invoices", []))

    dupes = [i for i in invoices if i in state.ARCHIVE]
    fresh = [i for i in invoices if i not in state.ARCHIVE]

    pay = esc = no = 0
    pay_eur = esc_eur = no_eur = 0.0
    for i in fresh:
        # skewed low, like the real population: many small, a few large
        base = seeded(i, 1, 1000)
        amount = round(70 + (base / 1000) ** 2.4 * 21000, 2)
        bucket = seeded(i + "v", 1, 100)
        if bucket <= 77:
            pay += 1; pay_eur += amount
        elif bucket <= 92:
            esc += 1; esc_eur += amount
        else:
            no += 1; no_eur += amount

    label = files[0]["name"] if len(files) == 1 else f"{len(files)} files"
    return {
        "label": label, "total": len(invoices), "size": human(size),
        "seconds": clock(max(1, round(len(fresh) * 4.8))),
        "pay": pay, "pay_eur": round(pay_eur, 2),
        "escalate": esc, "escalate_eur": round(esc_eur, 2),
        "patterns": min(esc, 2) if esc else 0,
        "nopay": no, "nopay_eur": round(no_eur, 2),
        "dupes": dupes, "rejected": rejected,
        "cost_per_invoice_usd": state.TOTALS["cost_per_invoice_usd"],
    }


# --------------------------------------------------------------------------- #
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(HERE), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("· " + fmt % args + "\n")

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _send(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\n\n")
        self.wfile.flush()

    def _answer(self, messages, rendered):
        """One reply: the panel first over the wire, the sentence streamed after.
        The page holds the panel back until the sentence lands."""
        t0 = time.time()
        try:
            self._send({"type": "blocks", "blocks": rendered})
            usage, got = {}, False
            for piece, final in stream_llm(messages):
                if piece is not None:
                    got = True
                    self._send({"type": "delta", "text": piece})
                else:
                    usage = final or {}
            if not got:
                self._send({"type": "delta", "text": "The model came back empty. What is below is still real."})
            self._send({"type": "done", "model": MODEL, "ms": int((time.time() - t0) * 1000),
                        "tokens": usage.get("total_tokens")})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:                                  # noqa: BLE001
            msg = re.sub(r"sk-[A-Za-z0-9_\-]+", "[key]", str(exc))[:200]
            try:
                self._send({"type": "delta",
                            "text": f"I could not reach the model ({msg}). What is below comes "
                                    f"straight from the desk, so it is still right."})
                self._send({"type": "done", "model": MODEL, "ms": int((time.time() - t0) * 1000), "error": True})
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _body(self, limit):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            return None
        return self.rfile.read(n)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/chat":
            return self._chat()
        if path == "/api/upload":
            return self._upload()
        if path == "/api/process":
            return self._process()
        if path == "/api/batch":
            return self._batch()
        if path == "/api/action":
            return self._action()
        return self.send_error(404)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)

        if path == "/api/invoices":
            return self._json(state.archive_page(
                q=(q.get("q") or [""])[0], action=(q.get("action") or [""])[0],
                vendor=(q.get("vendor") or [""])[0],
                limit=int((q.get("limit") or ["60"])[0]),
                offset=int((q.get("offset") or ["0"])[0])))

        if path == "/api/rules":
            return self._json(state.rules_view((q.get("v") or [""])[0],
                                               (q.get("vs") or [""])[0]))

        if path == "/api/summary":
            return self._json(state.summary())

        if path == "/api/lanes":
            return self._json(state.lanes(q=(q.get("q") or [""])[0],
                                          action=(q.get("action") or [""])[0]))

        if path == "/api/dossier":
            d = state.dossier(os.path.basename((q.get("file") or [""])[0]))
            return self._json(d) if d else self.send_error(404)

        # the real document, straight out of the box, read-only
        if path == "/api/pdf":
            name = os.path.basename((q.get("file") or [""])[0])
            target = (state.CAJA / name).resolve()
            if not str(target).startswith(str(state.CAJA.resolve())) or not target.is_file():
                return self.send_error(404)
            blob = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("Content-Disposition", f'inline; filename="{name}"')
            self.end_headers()
            return self.wfile.write(blob)

        if path == "/api/sepa.xml":
            body = state.sepa_xml().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Disposition", 'attachment; filename="remesa.xml"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        return super().do_GET()

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- a button. It moves real state and answers at once, no model in the way.
    def _action(self):
        try:
            req = json.loads(self._body(64 * 1024) or b"{}")
        except json.JSONDecodeError:
            return self.send_error(400)
        name = str(req.get("act") or "")
        out = (state.undo() if name == "undo"
               else state.act(name, req.get("key"), req.get("reason")))
        if out.get("refresh"):
            out["panels"] = {k: state.block(k) for k in ("queue", "payments", "report")}
        return self._json(out)

    # -- a question ---------------------------------------------------------
    def _chat(self):
        raw = self._body(256 * 1024)
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return self.send_error(400)
        text = str(req.get("text") or "").strip()
        if not text:
            return self.send_error(400)
        # a file named in the question wins: he is asking about that invoice
        named = named_file(text)
        if named:
            blocks, rendered = ["invoice"], [state.invoice_block(named)]
        else:
            blocks = route(text)
            rendered = [b for b in (state.block(n) for n in blocks) if b]
        self._sse_open()
        self._answer(build_messages(text, blocks, req.get("history") or [], named), rendered)

    # -- one file, read and inspected for real ------------------------------
    def _upload(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        name = (q.get("name") or ["file"])[0]
        name = os.path.basename(name)[:120] or "file"
        blob = self._body(MAX_UPLOAD)
        if blob is None:
            return self.send_error(413)
        info = inspect(name, blob)
        if info.get("ok"):
            info["key"] = hold(blob, info)
        out = json.dumps(info).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    # -- the run: every page inside what was dropped, read and judged one at
    # -- a time, each verdict going out the moment it exists -----------------
    def _process(self):
        try:
            req = json.loads(self._body(64 * 1024) or b"{}")
        except json.JSONDecodeError:
            return self.send_error(400)
        keys = [str(k) for k in (req.get("keys") or []) if str(k) in HELD]
        if not keys:
            return self.send_error(400)

        held = [HELD[k]["info"] for k in keys]
        label = held[0]["name"] if len(held) == 1 else f"{len(held)} files"
        size = sum(i.get("size", 0) for i in held)
        work = [pair for k in keys for pair in pages(k)]
        rejected = [list(r) for i in held for r in i.get("inside_rejected", [])]

        self._sse_open()
        t0 = time.time()
        rows, dupes = [], []
        try:
            self._send({"type": "open", "label": label, "total": len(work),
                        "size": human(size), "files": [i["name"] for i in held]})
            # read everything first, judge it together, then hand it over one at
            # a time in the order it was read
            order, parsed = [], []
            for name, blob in work:
                if name in state.ARCHIVE:
                    dupes.append(name)
                    order.append(("known", name))
                    continue
                try:
                    parsed.append(state.read_pdf(name, blob))
                except Exception as exc:                          # noqa: BLE001
                    rejected.append([name, str(exc)[:80] or "the page would not open"])
                    order.append(("reject", name))
                    continue
                order.append(("invoice", len(parsed) - 1))
            rows = state.judge_batch(parsed)

            for n, (what, ref) in enumerate(order, 1):
                if what == "invoice":
                    row = rows[ref]
                    out = {k: row[k] for k in ("file", "vendor", "number", "date", "total",
                                               "action", "blocking", "found", "scanned")}
                    # the rules it went through, so the page can draw the trace
                    out["rules"] = [[c["name"], c["verdict"]] for c in row["checks"]]
                    self._send({"type": "invoice", "n": n, "row": out})
                else:
                    self._send({"type": what, "n": n, "file": ref})

            counts = {a: sum(1 for r in rows if r["action"] == a)
                      for a in ("PAY", "ESCALATE", "DO NOT PAY")}
            eur = {a: round(sum(r["total"] or 0 for r in rows if r["action"] == a), 2)
                   for a in counts}
            reasons: dict = {}
            for r in rows:
                for b in r["blocking"]:
                    reasons[b] = reasons.get(b, 0) + 1

            run = {
                "label": label, "size": human(size), "total": len(rows),
                "suppliers": len({r["vendor"] for r in rows}),
                "seconds": elapsed(time.time() - t0),
                "pay": counts["PAY"], "pay_eur": eur["PAY"],
                "escalate": counts["ESCALATE"], "escalate_eur": eur["ESCALATE"],
                "nopay": counts["DO NOT PAY"], "nopay_eur": eur["DO NOT PAY"],
                "scans": sum(1 for r in rows if r["scanned"]),
                "dupes": dupes, "rejected": rejected,
                "reasons": sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])),
                "cost_per_invoice_usd": state.TOTALS["cost_per_invoice_usd"],
            }
            rid = hashlib.sha256((label + str(t0)).encode()).hexdigest()[:10]
            RUNS[rid] = {**run, "rows": rows}
            for stale in list(RUNS)[:-4]:
                RUNS.pop(stale, None)
            self._send({"type": "done", "run": rid, "summary": run})
        except (BrokenPipeError, ConnectionResetError):
            return

    # -- the batch result, then a sentence about it -------------------------
    def _batch(self):
        try:
            req = json.loads(self._body(1024 * 1024) or b"{}")
        except json.JSONDecodeError:
            return self.send_error(400)
        said = str(req.get("text") or "").strip()[:1500]
        run = RUNS.get(str(req.get("run") or ""))
        files = req.get("files") or []
        if not run and not files:
            return self.send_error(400)

        if run:
            b = {k: v for k, v in run.items() if k != "rows"}
            panel = state.run_block(run)
            caveat = ("Every verdict in it was read off the page and judged by the five rules, "
                      "so you may speak about it as work you did.")
        else:
            b = outcome(files)
            panel = state.batch_block(b)
            caveat = ("The verdicts in this batch are demo data and the panel says so; do not "
                      "claim you truly read the PDFs.")

        facts = json.dumps({**state.facts(), "the_batch_just_dropped": b}, ensure_ascii=False)
        msgs = [
            {"role": "system", "content": SYSTEM},
            {"role": "system", "content":
                f"FACTS (the only source of truth):\n{facts}\n\n"
                f"ON SCREEN under your sentence: the result of the batch Alberto just dropped "
                f"(counts, amounts, duplicates, files you could not read). Do not restate it.\n"
                + caveat
                + ("\nAlberto sent the batch with a message; answer THAT, using the batch result."
                   if said else "")},
            {"role": "user", "content": (
                said if said else
                f"I just sent you {b['label']}. In one or two sentences, what would you do first with it?")},
        ]
        self._sse_open()
        self._answer(msgs, [panel])

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    print(f"desk mock · http://127.0.0.1:{port}  · model {MODEL}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
