"""Single-call vision contract checks; no live provider requests."""

import json
from io import BytesIO

import pytest
from PIL import Image

from ingestion.config import settings
from ingestion.contracts import Contracts
from ingestion.providers.deepseek import ProviderError
from ingestion.providers.vision import VERSION, VisionReader


def png():
    stream = BytesIO()
    Image.new("RGB", (160, 220), "white").save(stream, format="PNG")
    return stream.getvalue()


def reader(value, *, finish_reason="stop"):
    calls = []

    async def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": json.dumps(value)},
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        }

    config = {
        **settings("deepseek"),
        "helmcode_base_url": "https://example.invalid",
        "api_key": "test",
        "vision_verify": True,
    }
    return VisionReader(config, request), calls


@pytest.mark.asyncio
async def test_one_request_preserves_rows_uncertainty_and_provenance():
    value = {
        "blocks": [
            {"kind": "line", "text": "Servicio 10.00", "uncertain": False},
            {"kind": "line", "text": "Servicio 10.00", "uncertain": False},
            {"kind": "footer", "text": "IBAN [unreadable]", "uncertain": True},
        ],
        "visual_notes": ["Reversed background bleed-through"],
    }
    provider, calls = reader(value)
    result = await provider.run(png(), page=1)
    assert len(calls) == len(result["raw"]["calls"]) == 1
    request = result["raw"]["calls"][0]["request"]
    assert request["model"] == settings("deepseek")["vision_model"]
    assert len(request["messages"][0]["content"]) == 5  # prompt + four views
    blocks = result["page"]["blocks"]
    assert len(blocks) == 3
    assert blocks[0]["text"] == blocks[1]["text"]
    assert blocks[2]["uncertainties"]
    assert blocks[0]["id"] == "p1-fg-b1"
    assert result["layout"]["blocks"]["p1-fg-b1"]["region_bbox"] == [0, 0, 160, 220]
    assert result["usage"] == {"prompt_tokens": 100, "completion_tokens": 20}
    assert (
        result["reader_version"]
        == settings("deepseek")["vision_reader_version"]
        == VERSION
    )
    Contracts().validate(
        "reading",
        {
            "schema_version": "0.1",
            "file_id": "test.pdf",
            "capabilities": {"block_kinds": True, "tables": False, "layout": False},
            "pages": [result["page"]],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value,finish_reason,code",
    [
        ({"blocks": []}, "stop", "empty_ocr_page"),
        ({"blocks": "invalid"}, "stop", "invalid_response"),
        (
            {"blocks": [{"kind": "line", "text": "x", "uncertain": "false"}]},
            "stop",
            "invalid_response",
        ),
        ({"blocks": []}, "length", "unsupported_size"),
    ],
)
async def test_invalid_output_fails_without_followup_calls(value, finish_reason, code):
    provider, calls = reader(value, finish_reason=finish_reason)
    with pytest.raises(ProviderError) as error:
        await provider.run(png())
    assert error.value.code == code
    assert len(calls) == 1
