from __future__ import annotations

import time
from datetime import datetime

import pytest
from test_backend_engine import runtime as backend_runtime

from backend import run_revision as rr
from backend import server
from backend.flujo import flujo
from backend.results_store import PostgresResultsStore
from ingestion.json_storage import PostgresJsonStorage

runtime = backend_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('review', [False, True])
async def test_read_api_has_bounded_queries_and_no_storage(runtime, monkeypatch, legacy, review, capsys):
    engine, kwargs, _, _, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', str(review).lower())
    if legacy:
        original = PostgresJsonStorage.object_key
        monkeypatch.setattr(PostgresJsonStorage, 'object_key',
                            lambda self, *args: original(self, *args).removeprefix('postgres/'))
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'

    def forbidden(*args, **kwargs):
        pytest.fail('API read attempted Storage access, initialization or evaluator replay')

    monkeypatch.setattr(engine.storage.client, 'get', forbidden)
    monkeypatch.setattr(engine.storage.client, 'post', forbidden)
    from ingestion import storage
    from rules_ingestion import evaluator
    monkeypatch.setattr(storage, 'SupabaseStorage', forbidden)
    monkeypatch.setattr(evaluator, 'evaluate', forbidden)
    store = PostgresResultsStore(repository=engine.repository)
    monkeypatch.setattr(server, 'STORE', store)
    monkeypatch.setattr(server, 'FACTURAS_DIR', kwargs['input_dir'])
    query_count = 0
    run = engine.repository._run

    def counted(operation):
        nonlocal query_count
        query_count += 1
        # Model 10 ms per database round trip; not a live latency claim.
        time.sleep(.01)
        return run(operation)

    monkeypatch.setattr(engine.repository, '_run', counted)
    observations = {}
    for name, limit, read in (
        ('summary', 1, server._resumen),
        ('detail', 3, lambda: store.get('invoice.pdf')),
        ('flow', 4, lambda: flujo(store, 'invoice.pdf')),
        ('run', 2, lambda: store.run_status(kwargs['request_key'])),
    ):
        query_count = 0
        start = time.monotonic()
        body = read()
        observations[name] = {'queries': query_count, 'seconds': round(time.monotonic() - start, 3)}
        assert body
        assert query_count <= limit
        if name == 'flow':
            assert body['evaluacion']['resultado']
            assert body['revision']['status'] == ('INCOMPLETE' if review else 'DISABLED')
            assert len(body['extraccion']['trabajos']) == 2
            for job in body['extraccion']['trabajos']:
                for attempt in job['intentos']:
                    assert isinstance(attempt['latency_seconds'], str)
                    for key in ('started_at', 'finished_at'):
                        assert attempt[key] == datetime.fromisoformat(attempt[key]).isoformat()
        if name == 'run':
            assert body == result
    assert store._engine is None
    with capsys.disabled():
        print({'legacy': legacy, 'review': review, 'reads': observations})


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', [False, True])
async def test_missing_or_corrupt_audit_never_falls_back_to_storage(runtime, monkeypatch, corrupt):
    engine, kwargs, _, _, _ = runtime
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    packet = engine.archive.load_packet(result['files'][0]['evaluation_record_id'])
    artifact_id = packet['evaluation']['artifact_id']
    engine.repository._query(
        'UPDATE ingestion.artifacts SET payload = %s::jsonb WHERE id = %s RETURNING id',
        ('{}' if corrupt else None, artifact_id))
    monkeypatch.setattr(engine.storage.client, 'get', lambda *a, **k: pytest.fail('Storage fallback'))
    monkeypatch.setattr(server, 'STORE', PostgresResultsStore(repository=engine.repository))
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._factura('invoice.pdf')
    assert responses[-1][0] == 503
    handler._flujo('invoice.pdf')
    status, body = responses[-1]
    assert status == 200
    assert 'code' in body['evaluacion']['resultado']
    assert body['salida']['verdict'] == 'ESCALAR'


def test_api_startup_and_health_need_only_postgres(runtime, monkeypatch):
    engine, _, _, _, _ = runtime
    from ingestion import storage
    from rules_ingestion import engine_storage

    monkeypatch.setattr(storage, 'SupabaseStorage', lambda *a, **k: pytest.fail('Storage initialization'))
    monkeypatch.setattr(engine_storage, 'EngineRepository', lambda: engine.repository)
    monkeypatch.setattr(server, 'STORE', None)
    assert server.get_store()._engine is None
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._salud()
    assert responses[-1][1]['ok'] is True
    assert responses[-1][1]['storage_bucket'] is None
