import json
from io import BytesIO

import httpx
import pytest
from PIL import Image

from ingestion.contracts import Contracts
from ingestion.providers.deepseek import ProviderError
from ingestion.providers.vision import VisionReader, checked_regions


@pytest.mark.parametrize(
    "regions",
    [
        [],
        [None],
        [{"layer": "foreground", "bbox": [-1, 0, 1, 1]}],
        [{"layer": "foreground", "bbox": [0, 0, 0, 1]}],
    ],
)
def test_reject_invalid_region_inventories(regions):
    with pytest.raises(ProviderError):
        checked_regions({"regions": regions})


@pytest.mark.asyncio
async def test_visual_reading_keeps_rows_layers_full_height_and_transform_evidence():
    calls = []
    native = [
        {
            "regions": [
                {"layer": "foreground", "bbox": [0.1, 0.1, 0.4, 0.4]},
                {"layer": "background_mirrored", "bbox": [0.6, 0.1, 0.9, 0.4]},
            ],
            "visual_notes": ["Shadow"],
        },
        {
            "blocks": [
                {"kind": "line", "text": "Service 10,00", "uncertain": False},
                {"kind": "line", "text": "Service 10,00", "uncertain": False},
                {"kind": "note", "text": "OK", "uncertain": False},
            ],
            "visual_notes": [],
        },
        {
            "blocks": [{"kind": "total", "text": "Total 999,00", "uncertain": True}],
            "visual_notes": [],
        },
    ]

    async def request(method, url, headers, body):
        calls.append(body)
        value = native[len(calls) - 1]
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "usage": {"prompt_tokens": 10},
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                ],
            },
        )

    png = BytesIO()
    Image.new("RGB", (100, 200), "white").save(png, format="PNG")
    result = await VisionReader(
        {
            "helmcode_base_url": "https://api.helmcode.com/v1",
            "api_key": "fixture",
            "vision_model": "gemma4",
            "vision_layout_model": "qwen3.6",
        },
        request,
    ).run(png.getvalue(), 1)
    reading = {
        "schema_version": "0.1",
        "file_id": "opaque.pdf",
        "capabilities": {"block_kinds": True, "tables": False, "layout": False},
        "pages": [result["page"]],
    }
    Contracts("benchmark/schemas").validate("reading", reading)
    blocks = result["page"]["blocks"]
    assert [b["text"] for b in blocks[:2]] == ["Service 10,00", "Service 10,00"]
    assert blocks[0]["id"] != blocks[1]["id"]
    assert "-bg-" in blocks[-1]["id"]
    assert blocks[-1]["uncertainties"]
    assert all(
        t["source_bbox_pixels"][3] == 200 for t in result["layout"]["transforms"]
    )
    assert result["usage"] == {"prompt_tokens": 30}
    assert len(result["raw"]["calls"]) == 3
    assert all("fixture" not in json.dumps(call) for call in result["raw"]["calls"])
    assert "opaque.pdf" not in json.dumps(calls)


@pytest.mark.asyncio
async def test_truncated_or_empty_model_output_is_failure():
    async def request(*args):
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": None}}]
            },
        )

    reader = VisionReader(
        {"helmcode_base_url": "https://api.helmcode.com/v1", "api_key": "fixture"},
        request,
    )
    with pytest.raises(ProviderError, match="truncated"):
        await reader.ask("read", [Image.new("RGB", (20, 20))], "qwen3.6")


def test_verification_requires_every_original_block_in_order():
    from ingestion.providers.vision import checked_verification

    original = [{"text": "Service 12.00"}, {"text": "small note"}]
    block = {
        "index": 0,
        "kind": "paragraph",
        "text": "Service 12.00",
        "uncertain": False,
        "reason": "Visible",
    }
    with pytest.raises(ProviderError, match="Incomplete"):
        checked_verification({"blocks": [block]}, original)
    with pytest.raises(ProviderError, match="index"):
        checked_verification({"blocks": [block, block]}, original)
    note = {
        "index": 1,
        "kind": "note",
        "text": "[unreadable]",
        "uncertain": True,
        "reason": "Smudged",
    }
    assert checked_verification({"blocks": [block, note]}, original) == [block, note]
