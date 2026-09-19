"""HTTP contract tests for the canonical review API.

These drive a real socket on purpose: the doc's acceptance checklist asks for
actual request/response serialization, not direct handler calls. The store is a
fake, so nothing here touches a paid provider or a shared database.
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


def _row(file_id, **over):
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
    def __init__(self, runs=None, sano=True):
        self.runs, self.sano = runs or {}, sano

    def preflight(self):
        if not self.sano:
            raise ConnectionError("postgres unreachable")

    def get_run(self, request_key):
        return self.runs.get(request_key)


class FakeEngine:
    def __init__(self, repository):
        self.repository = repository


class FakeStore:
    def __init__(self, rows, sano=True, detalle=None):
        self.rows = rows
        self.detalle = detalle or {}
        self.engine = FakeEngine(FakeRepository(sano=sano))

    def all(self):
        return dict(self.rows)

    def get(self, file_id):
        if file_id in self.detalle:
            return self.detalle[file_id]
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

        def call(path):
            try:
                with urllib.request.urlopen(base + path) as res:
                    return res.status, json.loads(res.read())
            except urllib.error.HTTPError as err:
                return err.code, json.loads(err.read())

        call.shutdown = httpd.shutdown
        return call

    yield _serve


def test_listado_pagina_y_filtra_sin_cargar_paquetes(api):
    rows = {f"f{n}.pdf": _row(f"f{n}.pdf", decision="PAGAR" if n % 2 else "ESCALAR") for n in range(5)}
    call = api(FakeStore(rows))

    status, body = call("/api/facturas?limit=2&offset=0")
    assert status == 200
    assert body["total"] == 5 and body["limit"] == 2 and len(body["filas"]) == 2

    _, page2 = call("/api/facturas?limit=2&offset=4")
    assert len(page2["filas"]) == 1

    _, filtered = call("/api/facturas?decision=ESCALAR")
    assert filtered["total"] == 3
    assert {f["evaluation"]["preliminary_decision"] for f in filtered["filas"]} == {"ESCALAR"}

    # A list row never carries the evidence packet.
    assert "evaluation_result" not in filtered["filas"][0]
    call.shutdown()


def test_listado_rechaza_paginacion_invalida(api):
    call = api(FakeStore({}))
    for bad in ("limit=0", "limit=500", "limit=abc", "offset=-1"):
        status, body = call(f"/api/facturas?{bad}")
        assert status == 422, bad
        assert body["error"] == "invalid_query"
    call.shutdown()


def test_pagar_con_revision_fallida_no_aparece_liquidada(api):
    """The checklist item: a failed review of a preliminary PAGAR is not cleared."""
    rows = {"x.pdf": _row("x.pdf", decision="PAGAR", estado="error",
                          review_status="FAILED", attention_required=True,
                          contextual_review={"status": "FAILED", "attention_required": True},
                          error="ReviewProviderError")}
    call = api(FakeStore(rows))
    _, body = call("/api/facturas")
    fila = body["filas"][0]

    assert fila["evaluation"]["preliminary_decision"] == "PAGAR"
    assert fila["payment_authorized"] is False
    assert fila["review"]["status"] == "FAILED"
    assert fila["review"]["attention_required"] is True
    assert fila["resolution"] is None
    assert fila["estado"] == "error"
    call.shutdown()


def test_ejes_se_reportan_por_separado(api):
    """Run, extraction, evaluation and review are different facts."""
    rows = {"y.pdf": _row("y.pdf", run_state="partial", run_error="BatchPartial",
                          extraction_status="needs_review")}
    call = api(FakeStore(rows))
    _, body = call("/api/facturas")
    fila = body["filas"][0]

    assert fila["run"] == {"state": "partial", "error": "BatchPartial"}
    assert fila["extraction"]["status"] == "needs_review"
    assert fila["evaluation"]["record_id"].startswith("er_")
    assert fila["review"]["status"] == "COMPLETED"
    call.shutdown()


def test_pdf_en_disco_sin_registro_es_pendiente(api, tmp_path):
    (tmp_path / "nueva.pdf").write_bytes(b"%PDF-1.4\n")
    call = api(FakeStore({}))
    _, body = call("/api/facturas")

    fila = body["filas"][0]
    assert fila["file_id"] == "nueva.pdf" and fila["estado"] == "pendiente"
    assert fila["evaluation"]["preliminary_decision"] is None
    # Absent review must read as "needs attention", never as cleared.
    assert fila["review"]["attention_required"] is True
    assert fila["payment_authorized"] is False
    call.shutdown()


def test_detalle_expone_identidades_exactas(api):
    detalle = _row("z.pdf") | {
        "raw_invoice": json.dumps({"invoice_number": "F-1"}),
        "checks": json.dumps([{"rule_id": "R1", "verdict": "PASS", "reason": "ok"}]),
        "evaluation_result": {"preliminary_decision": "PAGAR",
                              "completeness": {"approval_eligible": False}},
        "provenance": {"input_id": "in-1", "batch_id": "ba-1", "interpreter": "deepseek",
                       "evaluation_id": "ev_" + "c" * 64, "evaluation_date": "2026-09-19",
                       "captured_at": "2026-09-19T10:00:00Z", "original": None,
                       "outcome": None, "ruleset": RULESET,
                       "rule_sources": [{"name": "v3", "artifact_id": "a2",
                                         "sha256": "e" * 64, "byte_size": 9, "kind": "engine-rule-source"}]},
    }
    call = api(FakeStore({"z.pdf": _row("z.pdf")}, detalle={"z.pdf": detalle}))

    status, body = call("/api/factura/z.pdf")
    assert status == 200
    # A ruleset is pinned by hash, not by the name "v3".
    assert body["provenance"]["ruleset"]["sha256"] == "f" * 64
    assert body["provenance"]["rule_sources"][0]["sha256"] == "e" * 64
    assert body["provenance"]["evaluation_id"].startswith("ev_")
    assert body["checks"][0]["rule_id"] == "R1"
    assert body["payment_authorized"] is False
    assert body["evaluation"]["record_id"] == detalle["evaluation_record_id"]
    call.shutdown()


def test_detalle_rechaza_rutas_y_desconocidos(api):
    call = api(FakeStore({}))
    status, body = call("/api/factura/..%2Fetc%2Fpasswd")
    assert status == 422 and body["error"] == "invalid_file_id"

    status, _ = call("/api/factura/ausente.pdf")
    assert status == 404
    call.shutdown()


def test_salud_refleja_la_dependencia_real(api):
    call = api(FakeStore({}, sano=True))
    status, body = call("/api/salud")
    assert status == 200 and body["estado"] == "ok"
    assert body["dependencias"]["postgres"]["ok"] is True
    call.shutdown()

    caido = api(FakeStore({}, sano=False))
    status, body = caido("/api/salud")
    # A dead dependency is a degraded state, not a silent ok.
    assert status == 200 and body["estado"] == "degradado"
    assert body["dependencias"]["postgres"] == {"ok": False, "error": "ConnectionError"}
    caido.shutdown()


def test_ejecucion_desconocida_es_404(api):
    call = api(FakeStore({}))
    status, body = call("/api/ejecucion/no-existe")
    assert status == 404 and body["error"] == "run_not_found"
    call.shutdown()
