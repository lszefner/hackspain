"""HTTP contract tests for the canonical review API.

These drive a real socket on purpose: the doc's acceptance checklist asks for
actual request/response serialization, not direct handler calls. The store is a
fake, so nothing here touches a paid provider or a shared database.

The fake rows speak the store's Spanish vocabulary (`estado`, `hecha`) because
that is what `PostgresResultsStore` really returns; the assertions are on the
English contract the server publishes.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from backend import server

RULESET = {"artifact_id": "a1", "sha256": "f" * 64, "byte_size": 12, "kind": "decision-source-ruleset"}


def _stored(file_id, **over):
    base = {
        "file_id": file_id, "input_id": "in-1", "batch_id": "ba-1",
        "extraction_status": "completed", "extraction_error": None,
        "batch_status": "completed", "evaluation_record_id": "er_" + "a" * 64,
        "decision": "PAGAR", "review_record_id": "er_" + "b" * 64,
        "contextual_review": {"status": "COMPLETED", "attention_required": False},
        "run_state": "completed", "run_error": None,
        "estado": "hecha", "review_status": "COMPLETED", "attention_required": False,
        "error": None, "raw_invoice": None, "checks": None,
    }
    return base | over


class FakeRepository:
    def __init__(self, runs=None, healthy=True):
        self.runs, self.healthy = runs or {}, healthy

    def preflight(self):
        if not self.healthy:
            raise ConnectionError("postgres unreachable")

    def get_run(self, request_key):
        return self.runs.get(request_key)


class FakeEngine:
    def __init__(self, repository):
        self.repository = repository


class FakeStore:
    def __init__(self, rows, healthy=True, detail=None):
        self.rows = rows
        self.detail = detail or {}
        self.engine = FakeEngine(FakeRepository(healthy=healthy))

    def all(self):
        return dict(self.rows)

    def get(self, file_id):
        if file_id in self.detail:
            return self.detail[file_id]
        return self.rows.get(file_id)

    def health(self):
        try:
            self.engine.repository.preflight()
        except Exception as exc:  # noqa: BLE001 - mirrors the real store
            return {"postgres": {"ok": False, "error": type(exc).__name__}}
        return {"postgres": {"ok": True, "error": None}}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """Serve the real Handler on a real port, with the store injected."""
    monkeypatch.setattr(server, "FACTURAS_DIR", tmp_path)

    def _serve(store):
        monkeypatch.setattr(server, "STORE", store)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"

        def call(path, post=None):
            req = urllib.request.Request(
                base + path,
                data=post.encode() if post is not None else None,
                headers={"Content-Type": "application/x-www-form-urlencoded"}
                if post is not None else {})
            try:
                with urllib.request.urlopen(req) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        call.shutdown = httpd.shutdown
        return call

    yield _serve


def test_list_paginates_and_filters_without_loading_packets(api):
    rows = {f"f{n}.pdf": _stored(f"f{n}.pdf", decision="PAGAR" if n % 2 else "ESCALAR")
            for n in range(5)}
    call = api(FakeStore(rows))

    status, body = call("/api/invoices?limit=2&offset=0")
    assert status == 200
    assert body["total"] == 5 and body["limit"] == 2 and len(body["rows"]) == 2

    _, page2 = call("/api/invoices?limit=2&offset=4")
    assert len(page2["rows"]) == 1

    _, filtered = call("/api/invoices?decision=ESCALAR")
    assert filtered["total"] == 3
    assert {r["evaluation"]["preliminary_decision"] for r in filtered["rows"]} == {"ESCALAR"}

    # A list row never carries the evidence packet.
    assert "evaluation_result" not in filtered["rows"][0]
    call.shutdown()


def test_store_vocabulary_is_translated_at_the_boundary(api):
    """`hecha` is a SQLite column value; the published contract says `done`."""
    call = api(FakeStore({"a.pdf": _stored("a.pdf")}))

    _, body = call("/api/invoices")
    assert body["rows"][0]["status"] == "done"

    _, filtered = call("/api/invoices?status=done")
    assert filtered["total"] == 1

    _, detail = call("/api/invoice/a.pdf")
    assert detail["status"] == "done"

    _, summary = call("/api/summary")
    assert summary["counts"]["done"] == 1
    # The decision enum is the database check constraint, not a display string.
    assert summary["decisions"]["PAGAR"] == 1
    assert summary["batch_size"] == server.BATCH_SIZE
    call.shutdown()


def test_list_rejects_invalid_pagination(api):
    call = api(FakeStore({}))
    for bad in ("limit=0", "limit=500", "limit=abc", "offset=-1"):
        status, body = call(f"/api/invoices?{bad}")
        assert status == 422, bad
        assert body["error"] == "invalid_query" and body["field"]
    call.shutdown()


def test_pagar_with_failed_review_is_not_shown_as_cleared(api):
    """The checklist item: a failed review of a preliminary PAGAR is not cleared."""
    rows = {"x.pdf": _stored("x.pdf", decision="PAGAR", estado="error",
                             review_status="FAILED", attention_required=True,
                             contextual_review={"status": "FAILED", "attention_required": True},
                             error="ReviewProviderError")}
    call = api(FakeStore(rows))
    _, body = call("/api/invoices")
    row = body["rows"][0]

    assert row["evaluation"]["preliminary_decision"] == "PAGAR"
    assert row["payment_authorized"] is False
    assert row["review"]["status"] == "FAILED"
    assert row["review"]["attention_required"] is True
    assert row["resolution"] is None
    assert row["status"] == "error"
    call.shutdown()


def test_axes_are_reported_independently(api):
    """Run, extraction, evaluation and review are different facts."""
    rows = {"y.pdf": _stored("y.pdf", run_state="partial", run_error="BatchPartial",
                             extraction_status="needs_review")}
    call = api(FakeStore(rows))
    _, body = call("/api/invoices")
    row = body["rows"][0]

    assert row["run"] == {"state": "partial", "error": "BatchPartial"}
    assert row["extraction"]["status"] == "needs_review"
    assert row["evaluation"]["record_id"].startswith("er_")
    assert row["review"]["status"] == "COMPLETED"
    call.shutdown()


def test_pdf_on_disk_without_record_is_pending(api, tmp_path):
    (tmp_path / "new.pdf").write_bytes(b"%PDF-1.4\n")
    call = api(FakeStore({}))
    _, body = call("/api/invoices")

    row = body["rows"][0]
    assert row["file_id"] == "new.pdf" and row["status"] == "pending"
    assert row["evaluation"]["preliminary_decision"] is None
    # Absent review must read as "needs attention", never as cleared.
    assert row["review"]["attention_required"] is True
    assert row["payment_authorized"] is False
    call.shutdown()


def test_detail_exposes_exact_identities(api):
    detail = _stored("z.pdf") | {
        "raw_invoice": json.dumps({"invoice_number": "F-1"}),
        "checks": json.dumps([{"rule_id": "R1", "verdict": "PASS", "reason": "ok"}]),
        "evaluation_result": {"preliminary_decision": "PAGAR",
                              "completeness": {"approval_eligible": False}},
        "provenance": {"input_id": "in-1", "batch_id": "ba-1", "interpreter": "deepseek",
                       "evaluation_id": "ev_" + "c" * 64, "evaluation_date": "2026-09-19",
                       "captured_at": "2026-09-19T10:00:00Z", "original": None,
                       "outcome": None, "ruleset": RULESET,
                       "rule_sources": [{"name": "v3", "artifact_id": "a2", "sha256": "e" * 64,
                                         "byte_size": 9, "kind": "engine-rule-source"}]},
    }
    call = api(FakeStore({"z.pdf": _stored("z.pdf")}, detail={"z.pdf": detail}))

    status, body = call("/api/invoice/z.pdf")
    assert status == 200
    # A ruleset is pinned by hash, not by the name "v3".
    assert body["provenance"]["ruleset"]["sha256"] == "f" * 64
    assert body["provenance"]["rule_sources"][0]["sha256"] == "e" * 64
    assert body["provenance"]["evaluation_id"].startswith("ev_")
    assert body["checks"][0]["rule_id"] == "R1"
    assert body["fields"]["invoice_number"] == "F-1"
    assert body["payment_authorized"] is False
    assert body["evaluation"]["record_id"] == detail["evaluation_record_id"]
    call.shutdown()


def test_detail_rejects_paths_and_unknown_files(api):
    call = api(FakeStore({}))
    status, body = call("/api/invoice/..%2Fetc%2Fpasswd")
    assert status == 422 and body["error"] == "invalid_file_id"

    status, body = call("/api/invoice/missing.pdf")
    assert status == 404 and body["error"] == "invoice_not_found"
    call.shutdown()


def test_health_reflects_the_real_dependency(api):
    call = api(FakeStore({}, healthy=True))
    status, body = call("/api/health")
    assert status == 200 and body["status"] == "ok"
    assert body["dependencies"]["postgres"]["ok"] is True
    call.shutdown()

    down = api(FakeStore({}, healthy=False))
    status, body = down("/api/health")
    # A dead dependency is a degraded state, not a silent ok.
    assert status == 200 and body["status"] == "degraded"
    assert body["dependencies"]["postgres"] == {"ok": False, "error": "ConnectionError"}
    down.shutdown()


def test_unknown_run_is_404(api):
    call = api(FakeStore({}))
    status, body = call("/api/runs/does-not-exist")
    assert status == 404 and body["error"] == "run_not_found"
    call.shutdown()


def test_processing_status_is_separate_from_health(api):
    call = api(FakeStore({}))
    status, body = call("/api/status")
    assert status == 200 and body == {"processing": False, "error": None}
    call.shutdown()


def test_run_requires_an_explicit_request_key(api):
    """A retry must reuse an intent, so the key cannot be implicit."""
    call = api(FakeStore({}))

    status, body = call("/api/runs", post="target=one&file_id=a.pdf")
    assert status == 422
    assert body == {"ok": False, "error": "invalid_run_request"}

    # Valid key, but nothing to process: no thread is started.
    status, body = call("/api/runs", post="request_key=k-1")
    assert status == 200
    assert body == {"ok": False, "processing": False}
    call.shutdown()


def test_unknown_route_is_404(api):
    call = api(FakeStore({}))
    for path in ("/api/facturas", "/api/salud", "/api/resumen"):
        status, body = call(path)
        assert status == 404 and body["error"] == "not_found", path
    call.shutdown()
