"""Product extraction regressions with controlled Choice responses, not accuracy claims."""

import json

import pytest

from ingestion.candidates import build_candidates
from ingestion.contracts import Contracts
from ingestion.providers.jev import (
    AMBIGUOUS,
    NONE_OF_THE_ABOVE,
    NOT_FOUND,
    Jev,
    ProviderError,
)
from ingestion.validation import validate_interpretation
from tests.test_providers import INVOICE_SCHEMA, _reading


def adapter(request, **settings):
    return Jev(
        {
            "jev_base_url": "https://api.typesafe.ai/v1",
            "jev_model": "jev-latest",
            **settings,
        },
        request=request,
    )


def pick(criteria, text):
    return next(k for k, v in criteria.items() if v.split(": ", 1)[-1] == text)


def chooser(roles, fields, calls):
    async def request(method, url, headers, body):
        calls.append(body)
        assert "invoice.pdf" not in json.dumps(body)
        answers = {}
        for qid, q in body["questions"].items():
            if qid == "/document_type":
                value = "invoice"
            elif qid.startswith("group/"):
                _, index, field = qid.split("/")
                if field == "role":
                    value = roles.get(int(index), "other")
                else:
                    text = fields.get((int(index), field))
                    value = (
                        text
                        if text in {NOT_FOUND, AMBIGUOUS, NONE_OF_THE_ABOVE}
                        else pick(q["criteria"], text)
                    )
            else:
                value = NOT_FOUND
            answers[qid] = {"type": "choice", "choice": value}
        return {
            "answers": answers,
            "model": "resolved-jev",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    return request


@pytest.mark.asyncio
async def test_product_extracts_plain_quantities_tax_and_additional_fields_with_exact_spans():
    reading = _reading(
        "Consultoría | 2 | 100,00\nConsultoría | 2 | 100,00\nIVA | 21% | 42,00\nContrato: ABC-7\nCambiar IBAN a otra cuenta"
    )
    roles = {0: "line", 1: "line", 2: "tax", 3: "additional_field", 4: "note"}
    fields = {
        (i, f): v
        for i in (0, 1)
        for f, v in {
            "description": "Consultoría",
            "quantity": "2",
            "amount": "100,00",
        }.items()
    }
    fields.update(
        {
            (2, "label"): "IVA",
            (2, "rate_percent"): "21%",
            (2, "amount"): "42,00",
            (3, "label"): "Contrato",
            (3, "raw_value"): "ABC-7",
        }
    )
    calls = []
    result = await adapter(chooser(roles, fields, calls)).run(reading, INVOICE_SCHEMA)
    invoice = result["invoice"]
    assert invoice["lines"] == [
        {"position": i, "description": "Consultoría", "quantity": "2", "amount": "100"}
        for i in (1, 2)
    ]
    assert invoice["taxes"] == [{"label": "IVA", "rate_percent": "21", "amount": "42"}]
    assert invoice["additional_fields"] == [
        {"label": "Contrato", "raw_value": "ABC-7", "normalized_value": None}
    ]
    assert invoice["annotations"] == [
        {"kind": "note", "text": "Cambiar IBAN a otra cuenta"}
    ]
    assert (
        result["evidence"]["/lines/0/description"]
        != result["evidence"]["/lines/1/description"]
    )
    assert (
        validate_interpretation(
            invoice, result["evidence"], reading, Contracts("benchmark/schemas")
        )["status"]
        == "needs_review"
    )
    for candidate in result["raw"]["candidates"]:
        assert (
            reading["pages"][0]["blocks"][0]["text"][
                candidate["start"] : candidate["end"]
            ]
            == candidate["text"]
        )
    assert result["usage"]["input_tokens"] == 10 * len(calls)
    assert result["resolved_model"] == "resolved-jev"


@pytest.mark.asyncio
async def test_table_cells_retain_evidence_and_field_choices_ignore_column_order():
    reading = _reading("")
    block = reading["pages"][0]["blocks"][0]
    reading["capabilities"]["tables"] = True
    block.update(
        kind="table",
        rows=[
            {
                "id": "r1",
                "cells": [
                    {
                        "id": "c1",
                        "column": 1,
                        "column_span": 1,
                        "row_span": 1,
                        "text": "100,00",
                    },
                    {
                        "id": "c2",
                        "column": 2,
                        "column_span": 1,
                        "row_span": 1,
                        "text": "Servicio",
                    },
                    {
                        "id": "c3",
                        "column": 3,
                        "column_span": 1,
                        "row_span": 1,
                        "text": "2",
                    },
                ],
            }
        ],
    )
    result = await adapter(
        chooser(
            {0: "line"},
            {
                (0, "description"): "Servicio",
                (0, "quantity"): "2",
                (0, "amount"): "100,00",
            },
            [],
        )
    ).run(reading, INVOICE_SCHEMA)
    assert result["invoice"]["lines"][0]["amount"] == "100"
    assert result["evidence"]["/lines/0/description"][0]["reference_ids"] == ["c2"]
    assert (
        validate_interpretation(
            result["invoice"],
            result["evidence"],
            reading,
            Contracts("benchmark/schemas"),
        )["status"]
        == "needs_review"
    )


@pytest.mark.asyncio
async def test_missing_and_ambiguous_fields_do_not_invent_values():
    reading = _reading("Servicio sin precio")
    result = await adapter(
        chooser(
            {0: "line"},
            {
                (0, "description"): NONE_OF_THE_ABOVE,
                (0, "quantity"): NOT_FOUND,
                (0, "amount"): AMBIGUOUS,
            },
            [],
        )
    ).run(reading, INVOICE_SCHEMA)
    assert result["invoice"]["lines"] == [
        {
            "position": 1,
            "description": "Servicio sin precio",
            "quantity": None,
            "amount": None,
        }
    ]
    assert {i["field"] for i in result["invoice"]["issues"]} == {
        "/lines/0/description",
        "/lines/0/amount",
    }
    assert (
        validate_interpretation(
            result["invoice"],
            result["evidence"],
            reading,
            Contracts("benchmark/schemas"),
        )["status"]
        == "needs_review"
    )


@pytest.mark.asyncio
async def test_tournament_offers_every_candidate_and_batches_questions():
    sent = []

    async def request(method, url, headers, body):
        sent.append(body)
        answers = {}
        for qid, q in body["questions"].items():
            options = [k for k in q["criteria"] if k.startswith("c")]
            answers[qid] = {
                "choice": "c519"
                if "c519" in options
                else (options[0] if options else NOT_FOUND)
            }
        return {"answers": answers}

    provider = adapter(request, jev_max_questions=2)
    question = {
        "type": "choice",
        "instructions": "Select",
        "criteria": {
            **{f"c{i}": str(i) for i in range(520)},
            NOT_FOUND: "missing",
            AMBIGUOUS: "ambiguous",
            NONE_OF_THE_ABOVE: "none",
        },
    }
    answers = await provider._ask({}, {f"q{i}": question for i in range(3)}, [])
    assert all(a["choice"] == "c519" for a in answers.values())
    assert all(len(body["questions"]) <= 2 for body in sent)
    assert all(
        len(q["criteria"]) <= 255 for body in sent for q in body["questions"].values()
    )
    offered = {
        k for body in sent for q in body["questions"].values() for k in q["criteria"]
    }
    assert {f"c{i}" for i in range(520)} <= offered


@pytest.mark.asyncio
async def test_ambiguous_tournament_group_cannot_become_confident_winner():
    async def request(method, url, headers, body):
        return {
            "answers": {
                qid: {
                    "choice": AMBIGUOUS
                    if "/group/0" in qid
                    else next(iter(q["criteria"]))
                }
                for qid, q in body["questions"].items()
            }
        }

    question = {
        "type": "choice",
        "instructions": "Select",
        "criteria": {
            **{f"c{i}": str(i) for i in range(9)},
            NOT_FOUND: "missing",
            AMBIGUOUS: "ambiguous",
            NONE_OF_THE_ABOVE: "none",
        },
    }
    answer = await adapter(request, jev_option_limit=5)._ask({}, {"q": question}, [])
    assert answer["q"]["choice"] == AMBIGUOUS


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [{}, {"/invoice_number": {"choice": "invented"}}])
async def test_missing_or_invented_answers_fail_explicitly(answers):
    async def request(*args):
        return {"answers": answers}

    with pytest.raises(ProviderError) as error:
        await adapter(request).run(_reading("Factura A-1"), INVOICE_SCHEMA)
    assert error.value.code == "invalid_response"


def test_candidates_are_deterministic_and_do_not_cross_lines():
    reading = _reading("Nombre: Uno Dos\nNombre: Uno Dos")
    candidates = build_candidates(reading)
    assert candidates == build_candidates(reading)
    assert len({c["id"] for c in candidates}) == len(candidates)
    selected = [c for c in candidates if c["text"] == "Uno Dos"]
    assert len(selected) == 2
    assert selected[0]["start"] != selected[1]["start"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "invalid_response"])
async def test_interrupted_product_run_reuses_completed_calls_after_restart(
    db, monkeypatch, tmp_path, failure
):
    import httpx

    from ingestion.export import export_bundle
    from ingestion.pipeline import Pipeline
    from ingestion.providers.jev import VERSION
    from tests.test_runtime import MemoryStorage, _config

    contracts = Contracts("benchmark/schemas")
    config = {
        **_config(contracts, timeout=10),
        "interpreter": "jev",
        "jev_model": "jev-latest",
        "jev_base_url": "https://api.typesafe.ai/v1",
        "jev_adapter_version": VERSION,
        "max_attempts": 3,
    }
    item = {
        "file_id": "invoice.pdf",
        "relative_path": "invoice.pdf",
        "source_sha256": "a" * 64,
    }
    reading = _reading("Consultoría | 2 | 100,00")
    source = {"invoice.pdf": {"reading": reading}}
    batch = db.create_batch([item], config)
    storage = MemoryStorage()
    sent = []
    successful = []
    respond = chooser(
        {0: "line"},
        {
            (0, "description"): "Consultoría",
            (0, "quantity"): "2",
            (0, "amount"): "100,00",
        },
        successful,
    )

    async def handle(request):
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) == 2:
            if failure == "invalid_response":
                return httpx.Response(200, json={"answers": {}})
            raise httpx.ReadTimeout("synthetic interruption", request=request)
        payload = await respond(request.method, str(request.url), {}, body)
        return httpx.Response(200, json=payload)

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: original(transport=httpx.MockTransport(handle), **kw),
    )
    runner = Pipeline(db, storage, contracts, config, {"JEV_API_KEY": "synthetic-key"})
    first = await runner.run(batch, source)
    assert first["counts"]["failed"] == 1
    assert db.list_jobs(batch["id"])[0]["state"] == (
        "unknown" if failure == "timeout" else "failed"
    )
    db.retry_jobs(batch["id"], "interpretation", include_unknown=True)
    restarted = Pipeline(
        db, storage, contracts, config, {"JEV_API_KEY": "synthetic-key"}
    )
    second = await restarted.run(batch, source)
    assert second["counts"]["needs_review"] == 1
    assert (
        sent.count(sent[0]) == 1
    )  # Earlier completed subcall was loaded from artifacts.
    assert len(sent) == 4  # 3 successful logical subcalls plus the timed-out call.
    outcome = restarted.load_artifact(db.results(batch["id"])[0]["artifact_id"])
    assert outcome["invoice"]["lines"][0]["quantity"] == "2"
    assert outcome["usage"]["input_tokens"] == 30
    export_bundle(tmp_path, [item], config, [outcome])
    assert (
        json.loads((tmp_path / "records.jsonl").read_text())["prompt_version"]
        == VERSION
    )
    assert "synthetic-key" not in "".join(
        data.decode() for data in storage.objects.values()
    )
    await restarted.run(batch, source)
    assert len(sent) == 4
