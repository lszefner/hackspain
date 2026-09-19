import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ingestion import Contracts, InvoiceIngestion, api, blank_invoice
from ingestion.cli import main
from ingestion.contracts import default_schema_dir

ROOT = Path(__file__).parents[1]
BENCH_SCHEMAS = ROOT / "benchmark" / "schemas"


@pytest.fixture()
def dispatched(monkeypatch):
    mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(api, "execute_command", mock)
    return mock


async def test_ingest_passes_namespace_with_input_dir(dispatched):
    client = InvoiceIngestion()
    result = await client.ingest(input_dir="/tmp/invoices", dpi=300, concurrency=4)
    assert result == {"ok": True}
    args = dispatched.await_args.args[0]
    assert args.command == "ingest"
    assert args.input == "/tmp/invoices"
    assert args.manifest is None
    assert args.interpreter == "deepseek"
    assert args.ocr == "helmcode-vision"
    assert args.dpi == 300
    assert args.concurrency == 4
    assert args.schema_dir is None


async def test_ingest_manifest_path_and_schema_override(dispatched):
    client = InvoiceIngestion(schema_dir=Path("/schemas"))
    await client.ingest(manifest=Path("/tmp/m.json"), interpreter="jev", ocr="fal-got-v2")
    args = dispatched.await_args.args[0]
    assert args.manifest == "/tmp/m.json"
    assert args.input is None
    assert args.schema_dir == "/schemas"


async def test_status_resume_export_interpret_retry_namespaces(dispatched):
    client = InvoiceIngestion()
    await client.status("b1")
    assert dispatched.await_args.args[0].batch == "b1"
    await client.resume("b2")
    assert dispatched.await_args.args[0].command == "resume"
    await client.export("b3", Path("/tmp/out"))
    args = dispatched.await_args.args[0]
    assert args.command == "export" and args.output == "/tmp/out"
    await client.interpret("b4", interpreter="jev", reading_review=Path("/r.json"))
    args = dispatched.await_args.args[0]
    assert args.command == "interpret"
    assert args.reading_run == "b4"
    assert args.reading_review == "/r.json"
    await client.retry("b5", stage="reading", include_unknown=True)
    args = dispatched.await_args.args[0]
    assert args.command == "retry"
    assert args.include_unknown is True
    assert args.failed_only is False
    await client.retry("b6", stage="interpretation")
    args = dispatched.await_args.args[0]
    assert args.include_unknown is False
    assert args.failed_only is True


async def test_invalid_arguments_raise_before_dispatch(dispatched):
    client = InvoiceIngestion()
    with pytest.raises(ValueError):
        await client.ingest()
    with pytest.raises(ValueError):
        await client.ingest(input_dir="/a", manifest="/b")
    with pytest.raises(ValueError):
        await client.ingest(input_dir="/a", interpreter="other")
    with pytest.raises(ValueError):
        await client.ingest(input_dir="/a", ocr="other")
    with pytest.raises(ValueError):
        await client.ingest(input_dir="/a", dpi=150)
    with pytest.raises(ValueError):
        await client.ingest(input_dir="/a", concurrency=0)
    with pytest.raises(ValueError):
        await client.interpret("run", interpreter="bad")
    with pytest.raises(ValueError):
        await client.retry("b", stage="reading-extra")
    dispatched.assert_not_called()


async def test_execute_command_runs_off_event_loop(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    thread_ids = []

    def blocking(args):
        thread_ids.append(threading.get_ident())
        started.set()
        release.wait(timeout=5)
        return {"done": True}

    monkeypatch.setattr(api, "_run_command", blocking)
    task = asyncio.create_task(api.execute_command(object()))
    try:
        for _ in range(500):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert threading.get_ident() not in thread_ids
    finally:
        release.set()
    assert await asyncio.wait_for(task, timeout=5) == {"done": True}


async def test_execute_command_propagates_errors(monkeypatch):
    def failing(args):
        raise ValueError("boom")

    monkeypatch.setattr(api, "_run_command", failing)
    with pytest.raises(ValueError, match="boom"):
        await api.execute_command(object())


def test_default_contracts_match_benchmark_schema_hashes():
    default = Contracts()
    explicit = Contracts(BENCH_SCHEMAS)
    assert default.hashes == explicit.hashes
    assert set(default.schemas) == set(explicit.schemas)
    assert "invoice" in default.schemas


def test_blank_invoice_resolves_default_schema_from_foreign_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    invoice = blank_invoice("sample.pdf")
    assert invoice["file_id"] == "sample.pdf"
    assert invoice["document_type"] == "unknown"


def test_explicit_missing_schema_dir_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        Contracts(tmp_path / "absent")
    with pytest.raises(FileNotFoundError):
        blank_invoice("x.pdf", schema_dir=tmp_path / "absent")


def test_fixture_runs_without_dotenv_or_worker_deps(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "fixture-out"
    code = main(["fixture", "--output", str(output)])
    assert code == 0
    assert (output / "outcomes.jsonl").exists()


def test_missing_worker_deps_report_controlled_error(monkeypatch, capsys):
    def missing(args):
        raise ImportError("No module named 'httpx'")

    monkeypatch.setattr(api, "execute_command", missing)
    code = main(["status", "--batch", "dummy"])
    assert code == 2
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "worker_dependencies_missing"


def test_default_schema_dir_prefers_packaged_copy():
    assert default_schema_dir() in (
        Path(api.__file__).resolve().parent / "schemas",
        BENCH_SCHEMAS,
    )
