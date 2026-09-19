"""Desk tests. No network, no SMTP, no paid calls: every transport is faked
or absent, and a real socket never leaves the process.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import smtplib
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from decimal import Decimal
from email.message import Message
from xml.etree import ElementTree as ET

import pytest

from desk import agents, chat, engine, llm, mail, rules, state
from desk.ledger import Ledger

TODAY = "2026-09-19"

VENDOR = {"id": "P001", "razon_social": "Suministros & <Hermanos> S.L.",
          "nif": "B46102331", "iban": "ES2100491500051234567890",
          "condiciones": "60 dias"}
PEDIDO = {"pedido": "PO-2026-0164", "proveedor_id": "P001",
          "importe_total": "3531.16"}


def _invoice(fid="factura_0001.pdf", **kw):
    inv = {"file_id": fid, "invoice_number": "F-20261510", "vendor_id": "P001",
           "vendor": VENDOR["razon_social"], "vendor_email": "a@b.es",
           "nif": VENDOR["nif"], "iban": VENDOR["iban"], "pedido": "PO-2026-0164",
           "base": Decimal("2918.31"), "iva": Decimal("612.85"),
           "total": Decimal("3531.16"), "date": "2026-09-07",
           "currency": "EUR", "line_items": []}
    inv.update(kw)
    return inv


def decided(led, fid="factura_0001.pdf", *, inv=None, vendor=VENDOR,
            pedido=PEDIDO, erp_estado="PENDIENTE", hard=(), soft=()):
    """Record a real engine evaluation as a VERDICT, like ingest/seed do."""
    inv = inv or _invoice(fid)
    ev = engine.snapshot(inv, vendor, pedido, erp_estado=erp_estado,
                         today=TODAY, hard_matches=hard, soft_matches=soft)
    decision, checks = engine.evaluate(ev, rules.current(led))
    for c in checks:
        led.record("RULE_EVALUATED", file_id=fid, actor="engine",
                   ruleset_version="v3.1-balanced", **c)
    led.record("VERDICT", file_id=fid, actor="engine",
               ruleset_version="v3.1-balanced", decision=decision,
               deterministic=True, evaluation=ev, checks=checks,
               invoice={**engine.serial(inv), "vendor": inv.get("vendor"),
                        "vendor_email": inv.get("vendor_email")},
               driven_by=[c["canonical"] for c in checks if c["effect"] != "PAGAR"])
    return decision


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    for var in ("HELMCODE_BASE_URL", "HELMCODE_API_KEY", "HELMCODE_DEEPSEEK_MODEL",
                "DESK_SMTP_HOST", "DESK_SMTP_PORT", "DESK_SMTP_USERNAME",
                "DESK_SMTP_PASSWORD", "DESK_SMTP_FROM", "DESK_SMTP_STARTTLS",
                "DESK_SMTP_SSL", "DESK_EMAIL_TO", "DESK_REPORT_TO"):
        monkeypatch.delenv(var, raising=False)
    llm.reset()
    llm.configure(True)
    mail.configure(True)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network in test")))
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("smtp in test")))
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("smtp in test")))
    import httpx
    monkeypatch.setattr(httpx.AsyncClient, "request", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network in test")))
    yield


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / "t.db")


# ---------------------------------------------------------------- 1. ledger
class TestLedger:
    def test_append_only_triggers(self, led):
        led.record("VERDICT", file_id="f.pdf", decision="PAGAR")
        con = sqlite3.connect(led.path)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE events SET kind='X' WHERE seq=1")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM events WHERE seq=1")
        con.rollback()
        led.claim("k", "PAYMENT_REGISTERED")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE idempotency SET seq=9 WHERE key='k'")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM idempotency WHERE key='k'")
        con.close()

    def test_fold_parity_and_llm_costs(self, led):
        decided(led, "f1.pdf")
        led.record("LLM_CALL", file_id="f1.pdf", actor="llm", purpose="chat",
                   provider="helmcode", model="m", input_tokens=10,
                   output_tokens=5, cost_usd=0.01, cost_known=True)
        led.record("LLM_CALL", actor="llm", purpose="chat", provider="helmcode",
                   model="m", cost_usd=0.02, cost_known=True)
        rows = state.invoices(led)
        assert rows[0]["cost_usd"] == pytest.approx(0.01)
        assert state.get(led, "f1.pdf")["checks"] == rows[0]["checks"]
        s = state.summary(led)
        assert s["cost_usd"] == pytest.approx(0.03)

    def test_claim_linked_is_an_event(self, led):
        led.claim("k", "EMAIL_SENT")
        led.link_claim("k", 42)
        got = led.claimed("k")
        assert got["seq"] == 42
        assert led.by_kind("CLAIM_LINKED")[-1]["payload"]["claim_seq"] == 42

    def test_rule_apply_rolls_back_atomically(self, led, monkeypatch):
        decided(led, "f1.pdf")
        prop = rules.propose(led, "sube la tolerancia de importe a 25 EUR")
        calls = {"n": 0}
        real = led.record

        def boom(kind, *a, **k):
            if kind == "RULE_EVALUATED":
                calls["n"] += 1
                if calls["n"] == 2:
                    raise RuntimeError("crash mid-apply")
            return real(kind, *a, **k)
        monkeypatch.setattr(led, "record", boom)
        before = len(led.by_kind("VERDICT"))
        with pytest.raises(RuntimeError):
            rules.apply(led, prop)
        monkeypatch.undo()
        assert len(led.by_kind("VERDICT")) == before
        assert not led.by_kind("RULE_APPLIED")
        assert rules.current(led)["version"] == "v3.1-balanced"


# ---------------------------------------------------------------- 2. payment
class TestPayment:
    def test_same_approval_twice_and_different(self, led):
        decided(led, "f1.pdf")
        r1 = agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        assert r1["paid"] == ["f1.pdf"]
        r2 = agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        assert r2["blocked"] == ["f1.pdf"] and not r2["paid"]
        r3 = agents.approve(led, ["f1.pdf"], approval_id="AP-2")
        assert r3["blocked"] == ["f1.pdf"] and not r3["paid"]
        assert len(led.by_kind("PAYMENT_REGISTERED")) == 1
        assert len(led.by_kind("PAYMENT_BLOCKED")) == 2
        key = led.by_kind("PAYMENT_REGISTERED")[0]["payload"]["idempotency_key"]
        assert len(key) == 64

    def test_concurrent_approval_one_db(self, led):
        decided(led, "f1.pdf")
        other = Ledger(led.path)
        barrier = threading.Barrier(2)
        results = []

        def go(l):
            barrier.wait()
            results.append(agents.approve(l, ["f1.pdf"], approval_id="AP-X"))

        a, b = threading.Thread(target=go, args=(led,)), threading.Thread(target=go, args=(other,))
        a.start(); b.start(); a.join(); b.join()
        assert sum(len(r["paid"]) for r in results) == 1
        assert len(led.by_kind("PAYMENT_REGISTERED")) == 1

    def test_no_pagar_refused_even_with_override(self, led):
        decided(led, "f1.pdf", erp_estado="PAGADA")
        assert state.get(led, "f1.pdf")["decision"] == "NO_PAGAR"
        res = agents.approve(led, ["f1.pdf"], approval_id="AP-1", override=True)
        assert res["refused"] == ["f1.pdf"] and not res["paid"]
        assert led.by_kind("PAYMENT_REFUSED")
        assert not led.by_kind("PAYMENT_REGISTERED")

    def test_notice_queued_once_across_approve_and_recover(self, led, monkeypatch):
        monkeypatch.setenv("DESK_EMAIL_TO", "pagos@empresa.es")
        decided(led, "f1.pdf")
        agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        agents.recover_notices(led)
        agents.recover_notices(led)
        notices = [e for e in led.by_kind("EMAIL_QUEUED")
                   if e["payload"].get("payment_seq")]
        assert len(notices) == 1
        assert notices[0]["payload"]["to"] == "pagos@empresa.es"

    def test_concurrent_recover_one_notice(self, led, monkeypatch):
        monkeypatch.setenv("DESK_EMAIL_TO", "pagos@empresa.es")
        decided(led, "f1.pdf")
        row = state.get(led, "f1.pdf")
        led.register_payment("f1.pdf", row["ruleset_version"],
                             approval_id="AP-CRASH", actor="test")
        l2 = Ledger(led.path)
        results = []
        t1 = threading.Thread(target=lambda: results.append(agents.recover_notices(led)))
        t2 = threading.Thread(target=lambda: results.append(agents.recover_notices(l2)))
        t1.start(); t2.start(); t1.join(); t2.join()
        notices = [e for e in led.by_kind("EMAIL_QUEUED")
                   if e["payload"].get("payment_seq")]
        assert len(notices) == 1
        assert "no la ejecucion bancaria" in notices[0]["payload"]["body"]

    def test_notice_uses_payment_snapshot_not_current_invoice(self, led, monkeypatch):
        monkeypatch.setenv("DESK_EMAIL_TO", "pagos@empresa.es")
        decided(led, "f1.pdf")
        agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        decided(led, "f1.pdf",
                inv=_invoice("f1.pdf", iban="ES1111111111111111111111",
                             vendor_email="nuevo@x.es"))
        agents.recover_notices(led)
        body = led.by_kind("EMAIL_QUEUED")[-1]["payload"]["body"]
        assert "3.531,16" in body
        assert led.by_kind("EMAIL_QUEUED")[-1]["payload"]["to"] == "pagos@empresa.es"

    def test_unknown_file_is_refused_not_skipped(self, led):
        res = agents.approve(led, ["fantasma.pdf"], approval_id="AP-G")
        assert res["refused"] == ["fantasma.pdf"] and not res["paid"]
        ref = led.by_kind("PAYMENT_REFUSED")[-1]
        assert ref["file_id"] == "fantasma.pdf"

    def test_amount_zero_delta_uses_recorded_reason(self, led):
        decided(led, "f1.pdf")
        row = dict(state.get(led, "f1.pdf"))
        row["blocking"] = [{"canonical": "AMOUNT", "verdict": "FAIL",
                            "effect": "ESCALAR",
                            "reason": "el pedido no indica importe",
                            "detail": {"delta": "0.00",
                                       "pedido_importe": None}}]
        out = agents.investigate(led, row)
        assert out["finding"] == "el pedido no indica importe"
        assert out["recommendation"] is None

    def test_unknown_block_uses_recorded_reason(self, led):
        decided(led, "f1.pdf")
        row = dict(state.get(led, "f1.pdf"))
        row["blocking"] = [{"canonical": "AUTHORIZATION", "verdict": "UNKNOWN",
                            "effect": "ESCALAR", "reason": "umbral no evaluable",
                            "detail": {"x": 1}}]
        out = agents.investigate(led, row)
        assert out["finding"] == "umbral no evaluable"


# ---------------------------------------------------------------- 3. rules
class TestRules:
    def test_propose_backtest_apply_and_stale(self, led):
        decided(led, "f1.pdf",
                inv=_invoice("f1.pdf", total=Decimal("3531.16")),
                pedido={**PEDIDO, "importe_total": "3530.00"})
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-20261511"))
        assert state.get(led, "f1.pdf")["decision"] == "ESCALAR"
        assert state.get(led, "f2.pdf")["decision"] == "PAGAR"
        prop = rules.propose(led, "sube la tolerancia de importe a 25 EUR")
        assert prop and prop["params"]["amount_tolerance_eur"] == 25
        assert prop["diff"]
        flips = prop["backtest"]["flips"]
        assert [f["file_id"] for f in flips] == ["f1.pdf"]
        assert flips[0]["from"] == "ESCALAR" and flips[0]["to"] == "PAGAR"
        expected = {}
        for fid in ("f1.pdf", "f2.pdf"):
            row = state.get(led, fid)
            expected[fid], _ = engine.evaluate(
                row["evaluation"], {**rules.current(led), **prop["params"]})
        assert {f["file_id"]: f["to"] for f in flips} == {
            fid: d for fid, d in expected.items()
            if d != state.get(led, fid)["decision"]}
        res = rules.apply(led, prop)
        assert res["reprocessed"] == 2
        verdicts = led.by_kind("VERDICT")
        assert len([v for v in verdicts if v["file_id"] == "f1.pdf"]) == 2
        assert len([v for v in verdicts if v["file_id"] == "f2.pdf"]) == 2
        first = next(v for v in verdicts if v["file_id"] == "f1.pdf")
        assert first["payload"]["decision"] == "ESCALAR"
        assert state.get(led, "f1.pdf")["decision"] == "PAGAR"
        with pytest.raises(ValueError):
            rules.apply(led, prop)
        tampered = dict(prop, params={"amount_tolerance_eur": 999})
        with pytest.raises(ValueError):
            rules.apply(led, tampered)

    def test_authorization_raise_then_lower_clears_check(self, led):
        decided(led, "f1.pdf", inv=_invoice("f1.pdf", total=Decimal(20000),
                                          base=Decimal("16528.93"),
                                          iva=Decimal("3471.07")),
                pedido={**PEDIDO, "importe_total": "20000.00"})
        p1 = rules.propose(led, "por encima de 10000 EUR necesita segunda firma")
        assert p1 and p1["params"]["authorization_threshold_eur"] == 10000
        rules.apply(led, p1)
        row = state.get(led, "f1.pdf")
        assert row["decision"] == "ESCALAR"
        assert any(c["canonical"] == "AUTHORIZATION" for c in row["checks"])
        p2 = rules.propose(led, "por encima de 50000 EUR necesita segunda firma")
        rules.apply(led, p2)
        row = state.get(led, "f1.pdf")
        assert row["decision"] == "PAGAR"
        auth = [c for c in row["checks"] if c["canonical"] == "AUTHORIZATION"]
        assert not auth or all(c["verdict"] == "PASS" for c in auth)

    def test_engine_matches_frozen_run_checks(self, led):
        pytest.importorskip("rules_ingestion.checks")
        from rules_ingestion.checks import run_checks
        inv = _invoice("f1.pdf", base=2918.31, iva=612.85, total=3531.16)
        ev = engine.snapshot(_invoice("f1.pdf"), VENDOR, PEDIDO,
                             erp_estado="PENDIENTE", today=TODAY)
        _decision, checks = engine.evaluate(ev, rules.current(led))
        store = json.loads(
            __import__("pathlib").Path("desk/rules.store.json").read_text())
        master = {"proveedores": {VENDOR["id"]: VENDOR},
                  "proveedores_by_nif": {VENDOR["nif"]: VENDOR},
                  "pedidos": {PEDIDO["pedido"]: {**PEDIDO,
                                               "importe_total": 3531.16}},
                  "erp_estado": {PEDIDO["pedido"]: "PENDIENTE"},
                  "seen_invoice_keys": set(), "seen_amount_date": set(),
                  "today": TODAY}
        frozen_map = {c["canonical"]: c["verdict"]
                      for c in run_checks(inv, master, store)}
        engine_map = {c["canonical"]: c["verdict"] for c in checks}
        assert engine_map == frozen_map

    def test_rules_grouped_payload_preserves_events(self, led):
        from desk import explain
        decided(led, "f1.pdf")
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-2"))
        real = [e["seq"] for e in led.by_kind("RULE_EVALUATED")]
        nodes = [n for n in explain.timeline(led, "f1.pdf") if n["kind"] == "RULES"]
        assert len(nodes) == 1
        grouped = nodes[0]["payload"]["events"]
        mine = [e["seq"] for e in led.by_kind("RULE_EVALUATED")
                if e["file_id"] == "f1.pdf"]
        assert [n["seq"] for n in grouped] == mine
        assert len(real) > len(mine)
        for node, ev in zip(grouped, [e for e in led.all_events()
                                      if e["seq"] in mine]):
            assert node["payload"] == ev["payload"]
            assert node["cost_usd"] == ev["cost_usd"]
            assert node["ms"] == ev["ms"]


# ---------------------------------------------------------------- 4. llm
class _Resp:
    def __init__(self, payload, status_code=200):
        self._p = payload
        self.status_code = status_code

    def json(self):
        return self._p


def _chat_ok(opening="neutral", layout="original"):
    return _Resp({"choices": [{"message": {"content":
                 f'{{"opening":"{opening}","layout":"{layout}"}}'}}],
                  "usage": {"prompt_tokens": 7, "completion_tokens": 3}})


class TestLlm:
    def _enable(self, monkeypatch):
        monkeypatch.setenv("HELMCODE_BASE_URL", "https://api.helmcode.com/v1")
        monkeypatch.setenv("HELMCODE_DEEPSEEK_MODEL", "deepseek-v4-flash")
        monkeypatch.setenv("HELMCODE_API_KEY", "test-key")

    def test_config_absent_degrades_and_no_call(self, led):
        assert llm.status()["degraded"]
        decided(led, "f1.pdf")
        out = chat.respond(led, "que necesita mi atencion")
        assert "patrones" in out["text"] or "escaladas" in out["text"]
        assert not led.by_kind("LLM_CALL")

    def test_phrase_and_usage_accounting(self, led, monkeypatch):
        self._enable(monkeypatch)
        calls = []
        monkeypatch.setattr(llm, "_post",
                            lambda e, h, b: calls.append(b) or _chat_ok("concise"))
        from desk import llm_contract
        text = llm_contract.phrase(led, "chat", "Todo cuadra.", thread_id="t")
        assert text.startswith("En resumen:")
        ev = led.by_kind("LLM_CALL")[-1]
        assert ev["payload"]["input_tokens"] == 7
        assert ev["payload"]["cost_known"] is False
        assert ev["cost_usd"] == 0

    def test_provider_error_and_circuit(self, led, monkeypatch):
        self._enable(monkeypatch)
        from ingestion.providers.deepseek import ProviderError
        monkeypatch.setattr(llm, "_post",
                            lambda *a, **k: (_ for _ in ()).throw(
                                ProviderError("down", code="transport_error",
                                              retryable=True)))
        for _ in range(3):
            assert llm.complete(led, "chat", {"text": "hola"}) is None
        assert llm.status()["degraded"]
        n = len(led.by_kind("LLM_CALL"))
        assert llm.complete(led, "rule", {"sentence": "x", "allowed_keys": {}}) is None
        assert len(led.by_kind("LLM_CALL")) == n

    def test_forbidden_output_rejected(self, led, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _Resp(
            {"choices": [{"message": {"content": '{"opening":"PAGAR","layout":"original"}'}}],
             "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
        assert llm.complete(led, "chat", {"text": "hola"}) is None
        ev = led.by_kind("LLM_CALL")[-1]
        assert ev["payload"]["error_code"] == "invalid_schema"
        assert ev["payload"]["input_tokens"] == 1

    def test_retry_once_on_5xx(self, led, monkeypatch):
        self._enable(monkeypatch)
        calls = []

        def flaky(e, h, b):
            calls.append(1)
            if len(calls) == 1:
                return _Resp({}, status_code=500)
            return _chat_ok()
        monkeypatch.setattr(llm, "_post", flaky)
        assert llm.complete(led, "chat", {"text": "hola"}) is not None
        assert len(calls) == 2

    def test_trace_not_rephrased(self, led, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _chat_ok("helpful"))
        decided(led)
        out = chat.respond(led, "por que factura_0001.pdf")
        assert out["blocks"][0]["type"] == "trace"
        assert "factura_0001.pdf" in out["text"]

    def test_healthy_status_never_leaks_key(self, led, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _chat_ok())
        assert llm.complete(led, "chat", {"text": "hola"}) is not None
        st = llm.status()
        assert not st["degraded"] and st["reason"] is None and st["model"]
        brief = chat.brief(led)
        assert "test-key" not in json.dumps(brief)
        assert "test-key" not in json.dumps(st)
        assert "test-key" not in json.dumps(led.by_kind("LLM_CALL"))

    def test_hard_timeout_bounded(self, led, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(llm, "TIMEOUT", 0.01)
        import httpx

        class _C:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                await asyncio.sleep(60)

        monkeypatch.setattr(httpx, "AsyncClient", _C)
        started = __import__("time").monotonic()
        assert llm.complete(led, "chat", {"text": "hola"}) is None
        assert __import__("time").monotonic() - started < 5

    def test_malformed_cost_no_crash(self, led, monkeypatch):
        self._enable(monkeypatch)
        for bad in ("abc", -3, float("inf"), {"x": 1}):
            monkeypatch.setattr(llm, "_post", lambda *a, _bad=bad, **k: _Resp(
                {"choices": [{"message": {"content": '{"opening":"neutral","layout":"original"}'}}],
                 "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                 "cost_usd": _bad}))
            assert llm.complete(led, "chat", {"text": "h"}) is not None
            ev = led.by_kind("LLM_CALL")[-1]
            assert ev["cost_usd"] == 0 and ev["payload"]["cost_known"] is False

    def test_llm_down_full_flow(self, led, monkeypatch):
        self._enable(monkeypatch)
        from ingestion.providers.deepseek import ProviderError
        monkeypatch.setattr(llm, "_post", lambda *a, **k: (_ for _ in ()).throw(
            ProviderError("down", code="transport_error", retryable=True)))
        decided(led)
        out = chat.respond(led, "que necesita mi atencion")
        assert out["blocks"]
        trace = chat.respond(led, "por que factura_0001.pdf")
        assert trace["blocks"][0]["type"] == "trace"
        refused = chat.respond(led, "norma imposible sin parametros")
        assert "No he sabido" in refused["text"]
        assert refused["blocks"][0]["type"] == "rules"
        assert len(led.by_kind("PAYMENT_REGISTERED")) == 0
        assert len(led.by_kind("VERDICT")) == 1

    def test_rule_provider_never_applies(self, led, monkeypatch):
        self._enable(monkeypatch)
        decided(led, "f1.pdf")
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _Resp(
            {"choices": [{"message": {"content": '{"amount_tolerance_eur": 25}'}}],
             "usage": {}}))
        from desk import llm_contract
        parsed = llm_contract.parse_rule(led, "sube la tolerancia a 25")
        assert parsed == {"amount_tolerance_eur": 25}
        assert not led.by_kind("RULE_APPLIED")
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _Resp(
            {"choices": [{"message": {"content": '{"amount_tolerance_eur": 25, "backdoor": 1}'}}],
             "usage": {}}))
        assert llm_contract.parse_rule(led, "x") is None
        assert not led.by_kind("RULE_APPLIED")

    def test_malformed_choices_falls_back(self, led, monkeypatch):
        self._enable(monkeypatch)
        monkeypatch.setattr(llm, "_post", lambda *a, **k: _Resp(
            {"choices": [None], "usage": {}}))
        assert llm.complete(led, "chat", {"text": "h"}) is None
        assert led.by_kind("LLM_CALL")[-1]["payload"]["error_code"] == "invalid_json"


# ---------------------------------------------------------------- 5. remesa
class TestRemesa:
    def test_xml_parses_sums_and_escapes(self, led):
        decided(led, "f1.pdf")
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-99",
                                          total=Decimal("10.50")))
        agents.approve(led, ["f1.pdf", "f2.pdf"], approval_id="AP-1")
        _rid, xml = agents.remesa_xml(led)
        root = ET.fromstring(xml)
        ns = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}
        ctrl = Decimal(root.findtext(".//p:GrpHdr/p:CtrlSum", namespaces=ns))
        amounts = [Decimal(a.text) for a in root.findall(".//p:InstdAmt", ns)]
        assert ctrl == sum(amounts) == Decimal("3541.66")
        assert root.findtext(".//p:GrpHdr/p:NbOfTxs", namespaces=ns) == "2"
        assert "&amp;" in xml and "&lt;" in xml

    def test_snapshot_survives_changed_invoice(self, led):
        decided(led, "f1.pdf")
        agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        pay = led.by_kind("PAYMENT_REGISTERED")[0]["payload"]
        assert pay["creditor"]["iban"] == VENDOR["iban"]
        decided(led, "f1.pdf", inv=_invoice("f1.pdf", iban="ES1111111111111111111111"))
        _, xml = agents.remesa_xml(led)
        assert VENDOR["iban"] in xml and "ES1111111111111111111111" not in xml

    def test_default_remesa_is_latest(self, led, monkeypatch):
        decided(led, "f1.pdf")
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-2",
                                          total=Decimal("10.50")))
        import datetime as dt
        monkeypatch.setattr(agents, "_today", lambda: dt.date(2020, 1, 1))
        agents.approve(led, ["f1.pdf"], approval_id="AP-1")
        monkeypatch.undo()
        agents.approve(led, ["f2.pdf"], approval_id="AP-2")
        rid, xml = agents.remesa_xml(led)
        root = ET.fromstring(xml)
        ns = {"p": "urn:iso:std:iso:20022:tech:xsd:pain.001.001.03"}
        assert rid == "REM-20260919"
        assert root.findtext(".//p:GrpHdr/p:NbOfTxs", namespaces=ns) == "1"
        assert Decimal(root.findtext(".//p:GrpHdr/p:CtrlSum",
                                     namespaces=ns)) == Decimal("10.50")


# ---------------------------------------------------------------- 6. mail
class TestMail:
    def test_sandbox_transport_and_hold(self, led, monkeypatch):
        decided(led, "f1.pdf")
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        assert mail.status()["transport"] == "smtp-sandbox"
        n = agents.flush_outbox(led)
        assert n == 1
        sent = led.by_kind("EMAIL_SENT")
        assert sent[0]["payload"]["transport"] == "smtp-sandbox"
        assert "message_id" in sent[0]["payload"]

    def test_hold_and_cancel(self, led):
        decided(led, "f1.pdf")
        q = agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=3600)
        assert agents.flush_outbox(led, force=True) == 0
        assert agents.cancel_email(led, q["seq"]) is True
        assert agents.flush_outbox(led) == 0
        assert led.by_kind("EMAIL_CANCELLED")

    def test_failed_vs_sent_and_no_autoretry(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setattr(mail, "send",
                            lambda payload, **k: {"ok": False,
                                                  "code": "smtp_550",
                                                  "uncertain": False})
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        assert agents.flush_outbox(led) == 0
        assert led.by_kind("EMAIL_FAILED") and not led.by_kind("EMAIL_SENT")
        monkeypatch.setattr(mail, "send",
                            lambda payload, **k: {"ok": True, "transport": "smtp",
                                                  "message_id": "<x@y>"})
        assert agents.flush_outbox(led) == 0

    def test_uncertain_marked(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setattr(mail, "send",
                            lambda payload, **k: {"ok": False,
                                                  "code": "smtp_unknown_delivery",
                                                  "uncertain": True})
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        agents.flush_outbox(led)
        assert led.by_kind("EMAIL_UNCERTAIN")

    def test_report_one_digest_per_day(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setenv("DESK_REPORT_TO", "direccion@empresa.es")
        r1 = agents.send_report(led)
        assert r1["queued"] and r1["to"] == "direccion@empresa.es"
        r2 = agents.send_report(led)
        assert r2["duplicate"] and not r2.get("queued")
        queued = [e for e in led.by_kind("EMAIL_QUEUED")
                  if e["payload"].get("report_kind") == "daily_report"]
        assert len(queued) == 1
        assert not led.by_kind("REPORT_SENT")

    def test_cancel_before_dispatch_wins(self, led, monkeypatch):
        decided(led, "f1.pdf")
        sent = []
        monkeypatch.setattr(mail, "send",
                            lambda payload, **k: sent.append(payload)
                            or {"ok": True, "transport": "smtp-sandbox",
                                "message_id": "<m@x>"})
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        real = agents._claim_dispatch

        def cancel_first(ledger, seq):
            agents.cancel_email(ledger, seq)
            return real(ledger, seq)
        monkeypatch.setattr(agents, "_claim_dispatch", cancel_first)
        assert agents.flush_outbox(led) == 0
        assert not sent
        assert led.by_kind("EMAIL_CANCELLED")

    def test_cancel_after_dispatch_started_refused(self, led, monkeypatch):
        decided(led, "f1.pdf")
        started = threading.Event()
        release = threading.Event()
        seen = []

        def paused(payload, **k):
            started.set()
            assert release.wait(5)
            seen.append(payload)
            return {"ok": True, "transport": "smtp-sandbox", "message_id": "<m@x>"}
        monkeypatch.setattr(mail, "send", paused)
        q = agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        t = threading.Thread(target=lambda: agents.flush_outbox(led))
        t.start()
        assert started.wait(5)
        assert agents.cancel_email(led, q["seq"]) is False
        release.set(); t.join()
        assert len(seen) == 1

    def test_uncertain_not_retried_or_cancelled(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setattr(mail, "send",
                            lambda payload, **k: {"ok": False,
                                                  "code": "smtp_unknown_delivery",
                                                  "uncertain": True})
        q = agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        agents.flush_outbox(led)
        assert led.by_kind("EMAIL_UNCERTAIN")
        assert agents.flush_outbox(led) == 0
        assert agents.cancel_email(led, q["seq"]) is False

    def test_many_queued_all_cancellable(self, led):
        decided(led, "f1.pdf")
        seqs = [agents.queue_email(led, "f1.pdf", "request_po",
                                   hold_seconds=3600)["seq"] for _ in range(402)]
        assert agents.cancel_email(led, seqs[0]) is True
        assert agents.cancel_email(led, seqs[-1]) is True
        assert len(led.by_kind("EMAIL_CANCELLED")) == 2

    def test_sandbox_transport_pinned_on_payload(self, led, monkeypatch):
        decided(led, "f1.pdf")
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        monkeypatch.setenv("DESK_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("DESK_SMTP_FROM", "a@b.es")
        monkeypatch.setenv("DESK_SMTP_PORT", "25")
        mail.configure(True)
        calls = []
        monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: calls.append(a))
        assert agents.flush_outbox(led) == 1
        assert not calls
        assert led.by_kind("EMAIL_SENT")[0]["payload"]["transport"] == "smtp-sandbox"

    def test_real_smtp_path_and_override(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setenv("DESK_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("DESK_SMTP_FROM", "mesa@empresa.es")
        monkeypatch.setenv("DESK_SMTP_PORT", "587")
        monkeypatch.setenv("DESK_SMTP_USERNAME", "u")
        monkeypatch.setenv("DESK_SMTP_PASSWORD", "pw")
        monkeypatch.setenv("DESK_SMTP_STARTTLS", "1")
        monkeypatch.setenv("DESK_EMAIL_TO", "override@x.es")
        mail.configure(True)
        calls = []

        class _SMTP:
            def __init__(self, *a, **k):
                calls.append(("connect", a))
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def starttls(self, context=None):
                calls.append(("starttls",))
            def login(self, u, p):
                calls.append(("login", u, p))
            def send_message(self, msg):
                calls.append(("send", msg["To"]))
        monkeypatch.setattr(smtplib, "SMTP", _SMTP)
        agents.queue_email(led, "f1.pdf", "request_po", hold_seconds=0)
        assert agents.flush_outbox(led) == 1
        assert ("starttls",) in calls and any(c[0] == "login" for c in calls)
        assert ("send", "override@x.es") in calls
        sent = led.by_kind("EMAIL_SENT")[-1]
        assert sent["payload"]["transport"] == "smtp"
        assert "pw" not in json.dumps(led.all_events())

    def test_report_uses_override_recipient(self, led, monkeypatch):
        decided(led, "f1.pdf")
        monkeypatch.setenv("DESK_REPORT_TO", "direccion@empresa.es")
        monkeypatch.setenv("DESK_EMAIL_TO", "override@x.es")
        r = agents.send_report(led)
        assert r["queued"] and r["to"] == "override@x.es"

    def test_bad_smtp_port_is_config_error(self, led, monkeypatch):
        monkeypatch.setenv("DESK_SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("DESK_SMTP_FROM", "mesa@empresa.es")
        monkeypatch.setenv("DESK_SMTP_PORT", "not-a-port")
        mail.configure(True)
        assert mail.status()["error"] == "invalid_port"
        assert mail.status()["transport"] == "smtp-sandbox"
        out = mail.send({"to": "x@y.es", "subject": "s", "body": "b",
                         "transport": "smtp"})
        assert out == {"ok": False, "code": "invalid_port", "uncertain": False}

    def test_blocked_batching_one_email_per_cause(self, led):
        inv2 = _invoice("f2.pdf", invoice_number="F-2", pedido="PO-2026-0164")
        decided(led, "f1.pdf", erp_estado="PAGADA")
        decided(led, "f2.pdf", inv=inv2, erp_estado="PAGADA")
        out = agents.notify_blocked(led)
        assert len(out) == 1
        assert set(out[0]["file_ids"]) == {"f1.pdf", "f2.pdf"}
        body = led.by_kind("EMAIL_QUEUED")[-1]["payload"]["body"]
        assert "F-20261510" in body and "F-2" in body
        assert "Pueden confirmar" in body and "rechazad" not in body.lower()
        assert agents.notify_blocked(led) == []

    def test_blocked_new_file_produces_next_batch(self, led):
        decided(led, "f1.pdf", erp_estado="PAGADA")
        assert len(agents.notify_blocked(led)) == 1
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-9"),
                erp_estado="PAGADA")
        out = agents.notify_blocked(led)
        assert len(out) == 1 and out[0]["file_ids"] == ["f2.pdf"]
        ev = led.by_kind("EMAIL_QUEUED")[-1]
        assert ev["payload"]["file_ids"] == ["f2.pdf"]
        assert len(state.get(led, "f1.pdf")["emails"]) == 1
        assert len(state.get(led, "f2.pdf")["emails"]) == 1
        from desk import explain
        t1 = explain.timeline(led, "f1.pdf")
        t2 = explain.timeline(led, "f2.pdf")
        assert any(n["kind"] == "EMAIL_QUEUED" for n in t1)
        assert any(n["kind"] == "EMAIL_QUEUED" for n in t2)

    def test_cancel_then_new_blocked_retries(self, led):
        decided(led, "f1.pdf", erp_estado="PAGADA")
        agents.notify_blocked(led)
        q = led.by_kind("EMAIL_QUEUED")[-1]
        assert agents.cancel_email(led, q["seq"]) is True
        out2 = agents.notify_blocked(led)
        assert len(out2) == 1 and out2[0]["file_ids"] == ["f1.pdf"]
        assert led.by_kind("EMAIL_QUEUED")[-1]["seq"] != q["seq"]
        decided(led, "f2.pdf", inv=_invoice("f2.pdf", invoice_number="F-9"),
                erp_estado="PAGADA")
        agents.notify_blocked(led)
        last = led.by_kind("EMAIL_QUEUED")[-1]
        assert last["payload"]["file_ids"] == ["f2.pdf"]

    def test_notify_blocked_concurrent_one_batch(self, led):
        decided(led, "f1.pdf", erp_estado="PAGADA")
        l2 = Ledger(led.path)
        results = []
        t1 = threading.Thread(target=lambda: results.append(agents.notify_blocked(led)))
        t2 = threading.Thread(target=lambda: results.append(agents.notify_blocked(l2)))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert sum(len(r) for r in results) == 1
        queued = [e for e in led.by_kind("EMAIL_QUEUED")
                  if e["payload"].get("template") == "pause_review"]
        assert len(queued) == 1


# ---------------------------------------------------------------- 7. erp
class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code: str, retry_after=None):
    hdrs = Message()
    if retry_after is not None:
        hdrs["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(
        "http://x", 500, "err", hdrs,
        io.BytesIO(f"<error><codigo>{code}</codigo></error>".encode()))


class TestErp:
    def test_snapshot_retries_and_pages(self):
        from desk.erp import ErpClient
        sleeps = []
        tokens = iter(["T1", "T2"])
        calls = []

        def request(req, timeout=10):
            calls.append(req.full_url)
            if req.full_url.endswith("/erp/login"):
                return _FakeResp(f"<login><token>{next(tokens)}</token></login>".encode())
            page = req.full_url.rsplit("=", 1)[-1]
            if not hasattr(request, "failed"):
                request.failed = set()
            if page == "1" and "ora" not in request.failed:
                request.failed.add("ora")
                raise _http_error("ORA-00600")
            if page == "2" and "429" not in request.failed:
                request.failed.add("429")
                raise _http_error("ERP-429", retry_after=2)
            if page == "2" and "ses" not in request.failed:
                request.failed.add("ses")
                raise _http_error("SES-401")
            body = ("<root><meta><paginas>2</paginas><total>2</total></meta>"
                    "<asientos>" +
                    "".join(f"<asiento><id>AS{i}</id><pedido>PO{i}</pedido>"
                            f"<estado>PENDIENTE</estado><fecha>19/09/2026</fecha>"
                            f"<importe>1,00</importe><nif>B46102331</nif></asiento>"
                            for i in ([1] if page == "1" else [2])) +
                    "</asientos></root>")
            return _FakeResp(body.encode())

        client = ErpClient("http://127.0.0.1:8009", request=request,
                           sleep=sleeps.append)
        snap = client.snapshot()
        codes = [r["code"] for r in snap["retries"]]
        assert "ORA-00600" in codes and "ERP-429" in codes and "SES-401" in codes
        assert 2 in sleeps
        assert snap["pages"] == 2 and len(snap["entries"]) == 2
        assert all(r["path"].startswith("/erp/asientos") for r in snap["retries"])
        logins = [u for u in calls if u.endswith("/erp/login")]
        assert len(logins) == 2


# ---------------------------------------------------------------- 8. ingest
class TestIngest:
    def test_record_outcome_twice_and_engine_parity(self, led):
        pytest.importorskip("backend.local_backend")
        from desk import ingest
        fixture = {"status": "completed", "invoice": {
            "invoice_number": "F-20261510",
            "supplier": {"name": "Suministros Levante S.L.", "tax_id": "B46102331"},
            "payment": {"iban": "ES2100491500051234567890"},
            "purchase_order_reference": "PO-2026-0164",
            "totals": {"taxable_base": "2918.31", "total": "3531.16"},
            "taxes": [{"label": "IVA", "amount": "612.85"}],
            "issue_date": "07/09/2026", "currency": "EUR"}}
        snap = {"entries": [{"id": "AS1", "pedido": "PO-2026-0164",
                             "estado": "PENDIENTE"}], "retries": [], "pages": 1, "ms": 0}
        ok = ingest.record_outcome(led, "f1.pdf", fixture,
                                   {"P001": VENDOR}, {"PO-2026-0164": PEDIDO}, snap,
                                   source_sha256="abc", today=TODAY)
        assert ok
        ok2 = ingest.record_outcome(led, "f1.pdf", fixture,
                                    {"P001": VENDOR}, {"PO-2026-0164": PEDIDO}, snap,
                                    source_sha256="abc", today=TODAY)
        assert ok2
        verdicts = [v for v in led.by_kind("VERDICT") if v["file_id"] == "f1.pdf"]
        assert len(verdicts) == 2
        assert verdicts[0]["payload"]["decision"] == "PAGAR"
        row = state.get(led, "f1.pdf")
        assert row["decision"] == "PAGAR"
        dup = [c for c in row["checks"] if c["canonical"] == "DUPLICATES"]
        assert not dup or all(c["verdict"] == "PASS" for c in dup)
        ev = row["evaluation"]
        raw_decision, raw_checks = engine.evaluate(ev, rules.current(led))
        assert raw_decision == row["decision"]
        assert [c["canonical"] for c in raw_checks] == [c["canonical"] for c in row["checks"]]
        assert ev["invoice"]["total"] == "3531.16"

    def test_real_pipeline_with_fake_providers(self, led, tmp_path, monkeypatch):
        pytest.importorskip("backend.local_backend")
        import ingestion.pipeline as pipe_mod
        import ingestion.providers.deepseek as ds_mod
        import ingestion.providers.vision as vs_mod
        from backend.local_backend import LocalStorage
        from desk import ingest
        from ingestion.contracts import Contracts

        pdf = tmp_path / "f1.pdf"
        pdf.write_bytes(b"%PDF-fake-f1")
        sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
        monkeypatch.setattr(pipe_mod, "render_pdf",
                            lambda b, dpi: [{"page": 1, "width": 100, "height": 100,
                                             "rotation": 0, "image": b"png"}])
        text = ("F-20261510 Suministros Levante B46102331 PO-2026-0164 "
                "2026-09-07 ES2100491500051234567890 2918.31 612.85 3531.16 EUR")
        page_reading = {"page": 1, "blocks": [{"id": "p1-b1", "kind": "other",
                        "text": text, "rows": [], "uncertainties": []}],
                        "non_text_elements": []}

        async def vision_run(self, image, page_number):
            return {"text": text, "page": page_reading,
                    "layout": {"blocks": {"p1-b1": {"page": 1, "bbox": None,
                                                  "confidence": None,
                                                  "source": "vision"}}},
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
        monkeypatch.setattr(vs_mod.VisionReader, "run", vision_run)

        invoice = {"schema_version": "0.1", "file_id": "f1.pdf",
                   "document_type": "invoice", "invoice_number": "F-20261510",
                   "issue_date": "2026-09-07",
                   "purchase_order_reference": "PO-2026-0164", "currency": "EUR",
                   "supplier": {"name": "Suministros Levante S.L.",
                                "tax_id": "B46102331", "location": None,
                                "address": None},
                   "customer": {"name": "Cliente", "tax_id": None,
                                "location": None, "address": None},
                   "payment": {"iban": "ES2100491500051234567890"},
                   "lines": [{"position": 1, "description": "Material",
                              "quantity": "1", "amount": "2918.31"}],
                   "taxes": [{"label": "IVA", "rate_percent": "21",
                              "amount": "612.85"}],
                   "totals": {"taxable_base": "2918.31", "total": "3531.16"},
                   "annotations": [], "additional_fields": [], "issues": []}
        link = [{"page": 1, "reference_ids": ["p1-b1"]}]
        evidence = {p: link for p in (
            "/invoice_number", "/issue_date", "/purchase_order_reference",
            "/currency", "/document_type", "/supplier/name", "/supplier/tax_id",
            "/customer/name", "/payment/iban", "/lines/0/description",
            "/lines/0/quantity", "/lines/0/amount", "/taxes/0/label",
            "/taxes/0/rate_percent", "/taxes/0/amount",
            "/totals/taxable_base", "/totals/total")}

        async def ds_run(self, reading, schema):
            return {"invoice": dict(invoice), "evidence": evidence,
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4}}
        monkeypatch.setattr(ds_mod.DeepSeek, "run", ds_run)

        repo = ingest.DeskRepository(led)
        storage = LocalStorage(tmp_path / "objects")
        contracts = Contracts(str(ingest.ROOT / "benchmark" / "schemas"))
        config = {"version": "test", "interpreter": "deepseek",
                  "ocr": "helmcode-vision", "deepseek_model": "ds",
                  "vision_model": "vm", "dpi": 200, "timeout": 30,
                  "concurrency": 1, "schema_hashes": contracts.hashes,
                  "helmcode_base_url": "https://api.helmcode.com/v1"}
        pipeline = ingest.DeskPipeline(repo, storage, contracts, config,
                                       {"HELMCODE_API_KEY": "x"}, ledger=led)
        manifest = [{"file_id": "f1.pdf", "relative_path": "f1.pdf",
                     "local_path": str(pdf), "source_sha256": sha,
                     "ordinal": 0, "error": None}]
        batch = repo.create_batch(manifest, config)
        result = asyncio.run(pipeline.run(batch))
        assert result["counts"]["completed"] == 1
        row = next(iter(repo.results(batch["id"])))
        artifact = repo.get_artifact(row["artifact_id"])
        outcome = json.loads(storage.get(artifact["object_key"]))
        assert outcome["status"] == "completed"
        snap = {"entries": [{"id": "AS1", "pedido": "PO-2026-0164",
                             "estado": "PENDIENTE"}], "retries": [], "pages": 1, "ms": 0}
        ok = ingest.record_outcome(led, "f1.pdf", outcome,
                                   {"P001": VENDOR}, {"PO-2026-0164": PEDIDO}, snap,
                                   source_sha256=sha, today=TODAY)
        assert ok
        kinds = {e["kind"] for e in led.all_events()}
        assert {"EXTRACTED", "NORMALIZED", "ERP_RECONCILED", "VERDICT"} <= kinds
        row = state.get(led, "f1.pdf")
        assert row["decision"] == "PAGAR"
        assert any(e["payload"].get("cost_known") is False
                   for e in led.by_kind("EXTRACTED") + led.by_kind("NORMALIZED"))

    def test_billing_unknown_cost_visible(self):
        from desk.ingest import _billing
        got = _billing({"usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        assert got["cost_known"] is False and got["cost_usd"] == 0
        assert got["input_tokens"] == 5
