from __future__ import annotations

import asyncio
from argparse import Namespace
from pathlib import Path
from typing import Any, Literal

Interpreter = Literal["deepseek", "jev"]
Reader = Literal["fal-got-v2", "helmcode-vision"]
Stage = Literal["reading", "interpretation"]


def _run_command(args: Namespace) -> dict[str, Any]:
    from .pipeline import command

    return asyncio.run(command(args))


async def execute_command(args: Namespace) -> dict[str, Any]:
    return await asyncio.to_thread(_run_command, args)


class InvoiceIngestion:
    def __init__(self, *, schema_dir: str | Path | None = None) -> None:
        self.schema_dir = str(schema_dir) if schema_dir is not None else None

    async def _execute(self, command: str, **kwargs: Any) -> dict[str, Any]:
        return await execute_command(
            Namespace(command=command, schema_dir=self.schema_dir, **kwargs)
        )

    async def ingest(
        self,
        *,
        input_dir: str | Path | None = None,
        manifest: str | Path | None = None,
        interpreter: Interpreter = "deepseek",
        ocr: Reader = "helmcode-vision",
        dpi: int = 200,
        concurrency: int = 2,
    ) -> dict[str, Any]:
        if bool(input_dir) == bool(manifest):
            raise ValueError("Specify exactly one of input_dir or manifest")
        if interpreter not in ("deepseek", "jev"):
            raise ValueError("Unsupported interpreter")
        if ocr not in ("fal-got-v2", "helmcode-vision"):
            raise ValueError("Unsupported OCR reader")
        if dpi not in (200, 300) or not 1 <= concurrency <= 16:
            raise ValueError("dpi must be 200 or 300; concurrency must be 1..16")
        return await self._execute(
            "ingest",
            input=str(input_dir) if input_dir is not None else None,
            manifest=str(manifest) if manifest is not None else None,
            interpreter=interpreter,
            ocr=ocr,
            dpi=dpi,
            concurrency=concurrency,
        )

    async def status(self, batch_id: str) -> dict[str, Any]:
        return await self._execute("status", batch=batch_id)

    async def resume(self, batch_id: str) -> dict[str, Any]:
        return await self._execute("resume", batch=batch_id)

    async def export(self, batch_id: str, output: str | Path) -> dict[str, Any]:
        return await self._execute("export", batch=batch_id, output=str(output))

    async def interpret(
        self,
        reading_run: str,
        *,
        interpreter: Interpreter,
        reading_review: str | Path | None = None,
    ) -> dict[str, Any]:
        if interpreter not in ("deepseek", "jev"):
            raise ValueError("Unsupported interpreter")
        return await self._execute(
            "interpret",
            reading_run=reading_run,
            interpreter=interpreter,
            reading_review=(
                str(reading_review) if reading_review is not None else None
            ),
        )

    async def retry(
        self, batch_id: str, *, stage: Stage, include_unknown: bool = False
    ) -> dict[str, Any]:
        if stage not in ("reading", "interpretation"):
            raise ValueError("Unsupported retry stage")
        return await self._execute(
            "retry",
            batch=batch_id,
            stage=stage,
            include_unknown=include_unknown,
            failed_only=not include_unknown,
        )
