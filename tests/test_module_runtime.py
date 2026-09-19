import threading
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

import pytest

from ingestion import pdf, pipeline, storage
from ingestion.contracts import Contracts

ROOT = Path(__file__).parents[1]
BENCH_SCHEMAS = ROOT / "benchmark" / "schemas"


class FakeRepo:
    def __init__(self, *args, batch=None, error=None, **kwargs):
        self.batch = batch
        self.error = error
        self.close = Mock()

    def get_batch(self, batch_id):
        if self.error is not None:
            raise self.error
        return self.batch

    def list_jobs(self, batch_id):
        return []

    def results(self, batch_id):
        return []


async def test_status_command_closes_repo_once(monkeypatch):
    batch = {"id": "id", "status": "succeeded", "config": {}, "manifest": []}
    repo = FakeRepo(batch=batch)
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)
    result = await pipeline.command(Namespace(command="status", batch="id"))
    assert result["status"] == "succeeded"
    assert result["jobs"] == []
    repo.close.assert_called_once()


async def test_get_batch_error_still_closes_repo(monkeypatch):
    repo = FakeRepo(error=ValueError("boom"))
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)
    with pytest.raises(ValueError, match="boom"):
        await pipeline.command(Namespace(command="status", batch="id"))
    repo.close.assert_called_once()


def test_runtime_registers_repo_and_storage_close(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "credentials",
        lambda config, needs_ocr=True: {
            "SUPABASE_DB_URL": "dsn",
            "SUPABASE_URL": "url",
            "SUPABASE_SECRET_KEY": "key",
        },
    )
    repo = FakeRepo()
    client = Mock()
    storage_instance = Mock(client=client)
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)
    monkeypatch.setattr(pipeline, "SupabaseStorage", lambda *a, **k: storage_instance)
    with ExitStack() as resources:
        pipeline.runtime({}, Mock(), resources=resources)
    repo.close.assert_called_once()
    client.close.assert_called_once()


def test_runtime_storage_failure_still_closes_repo(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "credentials",
        lambda config, needs_ocr=True: {
            "SUPABASE_DB_URL": "dsn",
            "SUPABASE_URL": "url",
            "SUPABASE_SECRET_KEY": "key",
        },
    )
    repo = FakeRepo()
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)

    def failing_storage(*a, **k):
        raise RuntimeError("no storage")

    monkeypatch.setattr(pipeline, "SupabaseStorage", failing_storage)
    with ExitStack() as resources, pytest.raises(RuntimeError, match="no storage"):
        pipeline.runtime({}, Mock(), resources=resources)
    repo.close.assert_called_once()


def test_runtime_without_resources_keeps_old_behavior(monkeypatch):
    monkeypatch.setattr(
        pipeline,
        "credentials",
        lambda config, needs_ocr=True: {
            "SUPABASE_DB_URL": "dsn",
            "SUPABASE_URL": "url",
            "SUPABASE_SECRET_KEY": "key",
        },
    )
    repo = FakeRepo()
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)
    monkeypatch.setattr(pipeline, "SupabaseStorage", lambda *a, **k: Mock(client=Mock()))
    pipeline.runtime({}, Mock())
    repo.close.assert_not_called()


def test_repository_close_closes_pool_and_unregisters(monkeypatch):
    repo = storage.PostgresRepository(connection=Mock())
    repo._pool = Mock()
    unregister = Mock()
    monkeypatch.setattr(storage.atexit, "unregister", unregister)
    repo.close()
    repo._pool.close.assert_called_once()
    unregister.assert_called_once_with(repo.close)


def test_repository_close_unregisters_even_if_pool_close_raises(monkeypatch):
    repo = storage.PostgresRepository(connection=Mock())
    repo._pool = Mock()
    repo._pool.close.side_effect = RuntimeError("pool down")
    unregister = Mock()
    monkeypatch.setattr(storage.atexit, "unregister", unregister)
    with pytest.raises(RuntimeError, match="pool down"):
        repo.close()
    unregister.assert_called_once_with(repo.close)


async def test_export_command_closes_repo_and_storage(monkeypatch, tmp_path):
    contracts = Contracts(BENCH_SCHEMAS)
    batch = {
        "id": "id",
        "status": "succeeded",
        "config": {
            "schemas": contracts.schemas,
            "schema_hashes": contracts.hashes,
            "interpreter": "deepseek",
        },
        "manifest": [],
    }
    repo = FakeRepo(batch=batch)
    client = Mock()
    monkeypatch.setattr(pipeline, "PostgresRepository", lambda *a, **k: repo)
    monkeypatch.setattr(
        pipeline, "SupabaseStorage", lambda *a, **k: Mock(client=client)
    )
    output = tmp_path / "export-out"
    result = await pipeline.command(
        Namespace(command="export", batch="id", output=str(output))
    )
    repo.close.assert_called_once()
    client.close.assert_called_once()
    assert (output / "outcomes.jsonl").exists()
    assert result["output"] == str(output)


class CountingLock:
    def __init__(self):
        self._lock = threading.Lock()
        self.acquisitions = 0

    def __enter__(self):
        self._lock.acquire()
        self.acquisitions += 1
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


def test_render_pdf_serializes_pdfium_calls(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def stub(pdf_bytes, dpi, pdfium):
        calls.append(threading.get_ident())
        entered.set()
        release.wait(timeout=10)
        return []

    lock = CountingLock()
    monkeypatch.setattr(pdf, "_PDFIUM_LOCK", lock)
    monkeypatch.setattr(pdf, "_render_pdfium", stub)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(pdf.render_pdf, b"pdf", 200)
        assert entered.wait(timeout=10)
        second = pool.submit(pdf.render_pdf, b"pdf", 200)
        assert lock.acquisitions == 1
        release.set()
        assert first.result(timeout=10) == []
        assert second.result(timeout=10) == []
    assert len(calls) == 2


def test_render_pdf_releases_lock_on_render_error(monkeypatch):
    attempts = []

    def stub(pdf_bytes, dpi, pdfium):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("pdfium failed")
        return []

    monkeypatch.setattr(pdf, "_render_pdfium", stub)
    with pytest.raises(RuntimeError, match="pdfium failed"):
        pdf.render_pdf(b"pdf", 200)
    assert pdf.render_pdf(b"pdf", 200) == []
