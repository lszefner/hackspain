from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from threading import Event

import pytest
from test_backend_engine import runtime as backend_runtime
from test_decision_storage import _Client

from backend import run_revision as rr
from backend.export_outcomes import decide_output
from backend.flujo import flujo
from backend.results_store import PostgresResultsStore
from ingestion.artifact_cache import artifact_session
from ingestion.storage import SupabaseStorage
from rules_ingestion.decision_context import ContextError
from rules_ingestion.engine import InvoiceDecisionEngine

runtime = backend_runtime

@pytest.mark.asyncio
@pytest.mark.parametrize('json_storage', ['storage', 'postgres'])
async def test_review_disabled_is_durable_and_uses_evaluator(runtime, monkeypatch, json_storage):
    monkeypatch.setenv('REVISION_JSON_STORAGE', json_storage)
    engine, kwargs, calls, _, _ = runtime
    provider = kwargs.pop('review_provider')
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    import rules_ingestion.contextual_provider as cp
    monkeypatch.setattr(cp, 'review_provider_from_environment', lambda: pytest.fail('reviewer constructed'))
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert result['files'][0]['review_status'] == 'DISABLED'
    assert result['files'][0]['review_record_id'] is None
    assert provider.calls == 0
    store = PostgresResultsStore(engine)
    row = store.get('invoice.pdf')
    assert row['estado'] == 'hecha'
    assert row['review_status'] == 'DISABLED'
    assert decide_output(row)[0] == row['decision']
    assert decide_output(dict(row, decision='PAGAR')) == ('PAGAR', 'evaluator_review_disabled')
    view = flujo(store, 'invoice.pdf')
    assert view['revision']['status'] == 'DISABLED'
    assert view['etapas'][3]['estado'] == 'desactivada'
    assert await rr.revisar_lote(['invoice.pdf'], **kwargs) == result
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'true')
    kwargs['review_provider'] = provider
    with pytest.raises(ContextError, match='different inputs'):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    # A later environment change cannot reinterpret the saved output policy.
    assert decide_output(store.get('invoice.pdf'))[0] == row['decision']
    assert calls['generate'] == 1


@pytest.mark.asyncio
async def test_rules_reused_after_restart_and_invalidated_by_inputs(runtime, monkeypatch, tmp_path):
    engine, kwargs, calls, _, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    first = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert first['rules_reused'] is False
    fresh = InvoiceDecisionEngine(engine.repository, SupabaseStorage(
        url='https://storage.invalid', key='test-only', client=engine.storage.client))
    kwargs.update(engine=fresh, request_key='intent-restart')
    monkeypatch.delenv('JEV_API_KEY')
    second = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert second['rules_reused'] is True
    assert calls['generate'] == 1
    profile = tmp_path / 'profile.yaml'
    profile.write_text((rr.ROOT / 'rules_ingestion/profiles/balanced.yaml').read_text() + '\n# changed\n')
    kwargs.update(request_key='intent-changed', profile_path=str(profile))
    with pytest.raises(ValueError, match='cache miss'):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert calls['generate'] == 1
    monkeypatch.setenv('JEV_API_KEY', 'synthetic')
    kwargs['request_key'] = 'intent-changed-with-key'
    third = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert third['rules_reused'] is False
    assert calls['generate'] == 2


@pytest.mark.asyncio
async def test_unknown_generation_cannot_be_reissued_by_new_invoice(runtime, monkeypatch):
    _, kwargs, _, _, _ = runtime
    from rules_ingestion import build_rules
    attempts = []
    def fail(*args, **kw):
        attempts.append(1)
        raise OSError('provider outcome unknown')
    monkeypatch.setattr(build_rules, 'build_frozen_rules', fail)
    with pytest.raises(OSError):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    kwargs['request_key'] = 'another-invoice-intent'
    with pytest.raises(ContextError, match='generation in progress or unknown'):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_review_starts_before_next_evaluation_finishes(runtime, monkeypatch):
    engine, kwargs, _, _, _ = runtime
    Path(kwargs['input_dir'], 'second.pdf').write_bytes(b'%PDF-second')
    original_evaluate = engine.evaluate
    original_review = kwargs['review_provider'].review
    started = Event()
    evaluations = []
    def evaluate(**values):
        evaluations.append(1)
        if len(evaluations) == 2:
            assert started.wait(5), 'review was starved by synchronous evaluation'
        return original_evaluate(**values)
    async def review(request):
        started.set()
        await asyncio.sleep(0.01)
        return await original_review(request)
    monkeypatch.setattr(engine, 'evaluate', evaluate)
    monkeypatch.setattr(kwargs['review_provider'], 'review', review)
    result = await rr.revisar_lote(['invoice.pdf', 'second.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert len(evaluations) == 2


def test_verified_bytes_cached_only_inside_session(monkeypatch):
    client = _Client()
    storage = SupabaseStorage(url='https://storage.invalid', key='test', client=client)
    gets = []
    original = client.get
    def get(*args, **kwargs):
        gets.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(client, 'get', get)
    with artifact_session():
        ref = storage.put(b'original', 'test')
        assert storage.get(ref.object_key) == b'original'
        assert storage.get(ref.object_key) == b'original'
        assert len(gets) == 1
    client.objects[storage._url(ref.object_key)] = b'corrupt'
    with artifact_session():
        assert storage.get(ref.object_key) == b'corrupt'
        assert storage.get(ref.object_key) == b'corrupt'  # corrupt bytes never cached
    assert len(gets) == 3


@pytest.mark.asyncio
async def test_real_deterministic_invoice_with_network_latency(runtime, monkeypatch, capsys):
    monkeypatch.setenv('REVISION_JSON_STORAGE', 'postgres')
    engine, kwargs, calls, _, _ = runtime
    source = rr.ROOT / 'caja/facturas/2026-01-08_P001.pdf'
    if not source.is_file():
        pytest.skip('local sample PDF unavailable')
    Path(kwargs['input_dir'], 'invoice.pdf').write_bytes(source.read_bytes())
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    kwargs['request_key'] = 'latency-check'
    fresh = InvoiceDecisionEngine(engine.repository, SupabaseStorage(
        url='https://storage.invalid', key='test-only', client=engine.storage.client))
    kwargs['engine'] = fresh
    counts = {'get': 0, 'post': 0, 'db': 0}
    def delayed(target, name, delay, counter):
        original = getattr(target, name)
        def invoke(*args, **kw):
            counts[counter] += 1
            time.sleep(delay)
            return original(*args, **kw)
        monkeypatch.setattr(target, name, invoke)
    delayed(fresh.storage.client, 'get', 0.04, 'get')
    delayed(fresh.storage.client, 'post', 0.04, 'post')
    delayed(fresh.repository, '_run', 0.01, 'db')
    started = time.monotonic()
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    elapsed = time.monotonic() - started
    assert result['state'] == 'completed'
    assert result['rules_reused'] is True
    assert calls == {'read': 0, 'interpret': 0, 'generate': 1}
    with capsys.disabled():
        print(json.dumps({'simulated_seconds': round(elapsed, 3), 'network_calls': counts}))
    assert elapsed < 5


def test_erp_cache_preserves_capture_time_and_expires(runtime, monkeypatch):
    from dataclasses import asdict
    from datetime import UTC, datetime, timedelta

    from backend.erp_cache import capture_cached_erp
    from backend.master_data import ErpClient
    from ingestion.contracts import canonical_bytes, digest
    engine, _, _, _, _ = runtime
    calls = []
    def snapshot(self):
        calls.append(1)
        return {'records': [{'pedido': 'P1', 'estado': 'PENDIENTE'}],
                'pages': [], 'complete': True, 'error_code': None}
    monkeypatch.setattr(ErpClient, 'snapshot', snapshot)
    now = datetime.now(UTC)
    first, reused = capture_cached_erp(engine, captured_at=now.isoformat())
    assert not reused
    second, reused = capture_cached_erp(engine, captured_at=(now + timedelta(seconds=1)).isoformat())
    assert reused and second.captured_at == first.captured_at
    assert len(calls) == 1
    client = ErpClient()
    key = digest(canonical_bytes({'url': client.base_url, 'user': client.usuario}))
    old = asdict(first)
    old['captured_at'] = (now - timedelta(seconds=31)).isoformat()
    engine._put_json({'cache_key': key, 'snapshot': old}, 'engine-erp-cache')
    _, reused = capture_cached_erp(engine, captured_at=datetime.now(UTC).isoformat())
    assert not reused and len(calls) == 2


@pytest.mark.asyncio
async def test_stored_run_rules_pin_never_regenerates(runtime, monkeypatch):
    engine, kwargs, calls, _, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    monkeypatch.setenv('REVISION_RULESET_RUN_KEY', 'intent-1')
    monkeypatch.delenv('JEV_API_KEY')
    kwargs['request_key'] = 'pinned-stored-rules'
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed' and result['rules_reused']
    assert calls['generate'] == 1
    original = engine.repository.get_run('intent-1')
    pinned = engine.repository.get_run('pinned-stored-rules')
    old = engine._json(engine.archive.ref(str(original['input_artifact_id'])))
    new = engine._json(engine.archive.ref(str(pinned['input_artifact_id'])))
    assert old['ruleset'] == new['ruleset']
    assert old['rule_generation'] == new['rule_generation']


@pytest.mark.asyncio
async def test_compact_json_review_and_corruption_detection(runtime, monkeypatch):
    engine, kwargs, _, _, _ = runtime
    monkeypatch.setenv('REVISION_JSON_STORAGE', 'postgres')
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    review_id = result['files'][0]['review_record_id']
    review = engine.load(review_id)
    assert review['review']['object_key'].startswith('postgres/')
    assert PostgresResultsStore(engine).get('invoice.pdf')['review_status'] == 'INCOMPLETE'
    # A separate read re-verifies Postgres bytes instead of trusting the writer's cache.
    artifact_id = review['review']['artifact_id']
    engine.repository._query("UPDATE ingestion.artifacts SET payload = '{}'::jsonb WHERE id = %s RETURNING id", (artifact_id,))
    with pytest.raises((ValueError, ContextError), match='integrity'):
        engine.load(review_id)


@pytest.mark.asyncio
async def test_ingestion_reader_understands_compact_outcomes(runtime, monkeypatch):
    from ingestion.contracts import Contracts
    from ingestion.json_storage import PostgresJsonStorage
    from ingestion.pipeline import Pipeline
    from ingestion.storage import PostgresRepository
    engine, kwargs, _, _, _ = runtime
    monkeypatch.setenv('REVISION_JSON_STORAGE', 'postgres')
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    repository = PostgresRepository(engine.repository.dsn)
    try:
        storage = PostgresJsonStorage(engine.storage.storage, repository)
        row = repository.results(result['batch_id'])[0]
        runner = Pipeline(repository, storage, Contracts(str(rr.ROOT/'benchmark/schemas')), {'concurrency': 1})
        outcome = runner.load_artifact(row['artifact_id'])
        assert outcome['invoice']['file_id'] == 'invoice.pdf'
    finally:
        repository.close()


def test_paid_completion_keeps_lease_and_artifact_guards(runtime):
    from uuid import uuid4
    engine, _, _, _, _ = runtime
    repo = engine.repository
    batch = repo.create_batch([], {})
    entry = repo.register_input(batch['id'], 'test.pdf')
    job = repo.ensure_job(batch_id=batch['id'], input_id=entry['id'], stage='reading',
                          provider='scripted', model='test')
    lease = repo.claim(job_id=job['id'])
    repo.begin_attempt(job['id'], lease['lease_token'])
    artifact = engine._put_json({'reading': 'test'}, 'reading')
    with pytest.raises(PermissionError):
        repo.complete(job['id'], uuid4(), artifact['artifact_id'])
    with pytest.raises(ValueError, match='artifact'):
        repo.complete(job['id'], lease['lease_token'], uuid4())
    result = repo.complete(job['id'], lease['lease_token'], artifact['artifact_id'])
    assert result['state'] == 'succeeded'
    assert repo.list_attempts(job['id'])[0]['status'] == 'succeeded'
    with pytest.raises(PermissionError):
        repo.complete(job['id'], lease['lease_token'], artifact['artifact_id'])


@pytest.mark.asyncio
async def test_storage_mode_can_change_without_rewriting_immutable_artifacts(runtime, monkeypatch):
    engine, kwargs, _, _, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    monkeypatch.setenv('REVISION_JSON_STORAGE', 'postgres')
    first = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    first_packet = engine.load(first['files'][0]['evaluation_record_id'])
    monkeypatch.setenv('REVISION_JSON_STORAGE', 'storage')
    kwargs['request_key'] = 'storage-mode-changed'
    second = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert second['state'] == 'completed'
    second_packet = engine.load(second['files'][0]['evaluation_record_id'])
    assert first_packet['context_schema'] == second_packet['context_schema']
