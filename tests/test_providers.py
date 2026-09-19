import json
from pathlib import Path

import pytest

from ingestion.providers.deepseek import DeepSeek
from ingestion.providers.deepseek import ProviderError as DeepSeekError
from ingestion.providers.fal_ocr import FalOCR
from ingestion.providers.jev import Jev

ROOT = Path(__file__).parents[1]
INVOICE_SCHEMA = json.loads(
    (ROOT / "benchmark" / "schemas" / "invoice.json").read_text()
)


def _reading(text: str, file_id: str = "invoice.pdf") -> dict:
    return {
        "schema_version": "0.1",
        "file_id": file_id,
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [
            {
                "page": 1,
                "blocks": [
                    {
                        "id": "p1-b001",
                        "kind": "paragraph",
                        "text": text,
                        "rows": [],
                        "uncertainties": [],
                    }
                ],
                "non_text_elements": [],
            }
        ],
    }


@pytest.mark.asyncio
async def test_deepseek_requires_explicit_helmcode_config_and_strips_filename():
    with pytest.raises(DeepSeekError, match="explicit helmcode_base_url"):
        DeepSeek({"deepseek_model": "deepseek-v4-flash"})

    captured = {}

    async def request(method, url, headers, json_body):
        captured.update(method=method, url=url, headers=headers, body=json_body)
        assert "invoice.pdf" not in json.dumps(json_body)
        return {
            "model": "deepseek-v4-flash",
            "usage": {"input_tokens": 3, "output_tokens": 2},
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "invoice": {
                                    "schema_version": "0.1",
                                    "file_id": "wrong.pdf",
                                    "document_type": "unknown",
                                },
                                "evidence": {},
                            }
                        )
                    }
                }
            ],
        }

    result = await DeepSeek(
        {
            "helmcode_base_url": "https://api.helmcode.com/v1",
            "deepseek_model": "deepseek-v4-flash",
            "api_key": "test-key",
        },
        request=request,
    ).run(_reading("Factura: A-1"), INVOICE_SCHEMA)

    assert captured["method"] == "POST"
    assert captured["url"] == "https://api.helmcode.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    sent_state = json.loads(captured["body"]["messages"][1]["content"])
    assert "file_id" not in sent_state["reading"]
    assert result["invoice"]["file_id"] == "invoice.pdf"
    assert result["resolved_model"] == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_fal_saves_queue_id_before_polling_and_resume_uses_supplied_urls():
    events = []
    requests = []
    status_url = "https://queue.fal.run/fal-ai/got-ocr/v2/requests/request-1/status"
    response_url = "https://queue.fal.run/fal-ai/got-ocr/v2/requests/request-1/response"

    async def request(method, url, headers, json_body):
        events.append("request")
        requests.append((method, url, json_body))
        if method == "POST":
            return {
                "request_id": "request-1",
                "status_url": status_url,
                "response_url": response_url,
            }
        if url == status_url:
            return {"status": "COMPLETED"}
        assert url == response_url
        return {"outputs": ["FACTURA A-1"]}

    saved = []

    async def on_request_id(request_id, metadata):
        events.append("saved")
        saved.append((request_id, metadata))

    adapter = FalOCR(
        {
            "fal_base_url": "https://queue.fal.run",
            "fal_model": "fal-ai/got-ocr/v2",
            "fal_poll_interval": 0,
        },
        request=request,
        on_request_id=on_request_id,
    )
    result = await adapter.run(b"image-bytes")

    assert result["text"] == "FACTURA A-1"
    assert events == ["request", "saved", "request", "request"]
    assert saved[0][0] == "request-1"
    assert saved[0][1]["status_url"] == status_url
    assert saved[0][1]["response_url"] == response_url
    assert [item[1] for item in requests[1:]] == [status_url, response_url]

    resume_requests = []

    async def resume_request(method, url, headers, json_body):
        resume_requests.append((method, url))
        if url == status_url:
            return {"status": "COMPLETED"}
        assert url == response_url
        return {"outputs": ["RESUMED"]}

    resumed = await FalOCR(
        {
            "fal_base_url": "https://queue.fal.run",
            "fal_model": "fal-ai/got-ocr/v2",
            "fal_poll_interval": 0,
        },
        request=resume_request,
    ).resume("request-1", {"status_url": status_url, "response_url": response_url})
    assert resumed["text"] == "RESUMED"
    assert [method for method, _ in resume_requests] == ["GET", "GET"]
    assert all(method != "POST" for method, _ in resume_requests)


@pytest.mark.asyncio
async def test_jev_selects_repeated_line_groups_and_field_candidates():
    reading = _reading("Factura: INV-1\nServicio repetido 1,00\nServicio repetido 2,00")
    captured = {}

    async def request(method, url, headers, json_body):
        captured["body"] = json_body
        answers = {}
        for question_id, question in json_body["questions"].items():
            criteria = question["criteria"]
            if question_id == "/document_type":
                answers[question_id] = {"choice": "invoice"}
            elif question_id == "/invoice_number":
                wanted = next(
                    candidate_id
                    for candidate_id, description in criteria.items()
                    if description.split(": ", 1)[-1] == "INV-1"
                )
                answers[question_id] = {"choice": wanted}
            elif question_id.startswith("group/") and question_id.endswith("/role"):
                group_index = int(question_id.split("/")[1])
                answers[question_id] = {
                    "choice": "line" if group_index in {1, 2} else "other"
                }
            elif question_id.startswith("group/"):
                group_index = int(question_id.split("/")[1])
                field = question_id.split("/")[2]
                text = {
                    "description": "Servicio repetido",
                    "amount": f"{group_index},00",
                }.get(field)
                choice = next(
                    (
                        k
                        for k, v in criteria.items()
                        if text and v.endswith(": " + text)
                    ),
                    "__not_found__",
                )
                answers[question_id] = {"choice": choice}
            else:
                answers[question_id] = {"choice": "__not_found__"}
        return {"model": "jev-latest", "answers": answers, "usage": {"input_tokens": 1}}

    result = await Jev(
        {
            "jev_base_url": "https://api.typesafe.ai/v1",
            "jev_model": "jev-latest",
        },
        request=request,
    ).run(reading, INVOICE_SCHEMA)

    assert captured["body"]["state"]["reading"].get("file_id") is None
    assert result["invoice"]["invoice_number"] == "INV-1"
    assert result["invoice"]["lines"] == [
        {
            "position": 1,
            "description": "Servicio repetido",
            "quantity": None,
            "amount": "1",
        },
        {
            "position": 2,
            "description": "Servicio repetido",
            "quantity": None,
            "amount": "2",
        },
    ]
    assert (
        result["raw"]["line_groups"][0]["id"] != result["raw"]["line_groups"][1]["id"]
    )
    assert "/lines/0/description" in result["evidence"]
    assert "/lines/1/description" in result["evidence"]
