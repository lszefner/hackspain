from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_decision_context import (
    CAPTURED,
    EVAL_DATE,
    make_outcome,
    make_ruleset,
    make_snapshots,
)
from test_decision_storage import _Client

from ingestion.contracts import canonical_bytes, digest
from ingestion.storage import SupabaseStorage
from rules_ingestion.contextual_review import ProviderReply, ReviewLimits
from rules_ingestion.decision_context import ContextError
from rules_ingestion.engine import InvoiceDecisionEngine, RuleSource
from rules_ingestion.engine_storage import ArchiveError, EngineRepository
from tests.test_core_engine_review import REVIEWED, ScriptedProvider


@pytest.fixture
def seeded(db):
    repo = EngineRepository(db.dsn)
    storage = SupabaseStorage(url='https://storage.invalid', key='test-only', client=_Client())
    engine = InvoiceDecisionEngine(repo, storage)
    batch = repo.create_batch([], {})
    original = engine.archive.put(b'%PDF-1.4\nsynthetic contract document', 'original', 'application/pdf')
    row = repo.register_input(batch['id'], 'f-clean', content_hash=original['sha256'],
                              object_key=original['object_key'], size_bytes=original['byte_size'])
    render = engine.archive.put(b'synthetic-render', 'render', 'image/png')
    raw = engine.archive.put(b'raw provider bytes', 'provider-response', 'application/octet-stream')
    outcome = make_outcome()
    outcome['layout'] = {'pages': [{'image_artifact_id': render['artifact_id']}]}
    outcome['raw'] = {'ocr': [{'text': 'literal raw OCR text'}], 'interpretation': {'raw': 'response'}}
    reading = engine._put_json(outcome['reading'], 'reading', [original['artifact_id']])
    job = repo.ensure_job(batch_id=batch['id'], input_id=row['id'], stage='reading',
                          input_artifact_hash=original['sha256'], provider='scripted', model='test')
    claimed = repo.claim(job_id=job['id'])
    attempt = repo.begin_attempt(job['id'], claimed['lease_token'])
    repo.record_attempt_artifact(attempt['id'], raw['artifact_id'])
    repo.complete(job['id'], claimed['lease_token'], reading['artifact_id'])
    outcome_ref = engine._put_json(outcome, 'outcome')
    repo.set_result(row['id'], 'deepseek', outcome_ref['artifact_id'], 'completed', job_id=job['id'])
    kwargs = {'input_id': str(row['id']), 'interpreter': 'deepseek', 'ruleset': canonical_bytes(make_ruleset()),
              'rule_sources': [RuleSource('master.xlsx', b'PK\x03\x04synthetic workbook'),
                              RuleSource('rules.csv', b'Name, Value\r\nRule, 42\r\n', 'text/csv')],
              'snapshots': make_snapshots(), 'evaluation_date': EVAL_DATE, 'captured_at': CAPTURED}
    yield engine, kwargs
    repo.close()


def test_archive_roundtrip_and_exact_bytes(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    assert packet['record_id'] == engine.evaluate(**kwargs)['record_id']
    assert packet['kind'] == 'evaluation'
    assert engine._json(packet['context'])['schema_version'] == 'decision-context/2'
    assert engine._json(packet['evaluation'])['schema_version'] == 'evaluation-result/1'
    assert engine._json(packet['outcome'])['raw']['ocr'][0]['text'] == 'literal raw OCR text'
    refs = packet['extraction_artifacts']
    assert {'original', 'outcome', 'reading', 'render', 'provider-response'} <= {ref['kind'] for ref in refs}
    for source in packet['rule_source_lineage']['sources']:
        supplied = next(item for item in kwargs['rule_sources'] if item.name == source['name'])
        assert engine.archive.get({key: value for key, value in source.items() if key != 'name'}) == supplied.content
    repo = EngineRepository(engine.repository.dsn)
    try:
        fresh = InvoiceDecisionEngine(repo, engine.storage)
        assert fresh.load(packet['record_id']) == packet
    finally:
        repo.close()


def test_new_inputs_preserve_old_records(seeded):
    engine, kwargs = seeded
    first = engine.evaluate(**kwargs)
    dated = engine.evaluate(**(kwargs | {'evaluation_date': '2026-09-20'}))
    source_changed = engine.evaluate(**(kwargs | {'rule_sources': [RuleSource('rules.csv', b'changed')]}))
    rules = make_ruleset()
    rules['rules'][0]['on_fail'] = 'NO_PAGAR'
    rules_changed = engine.evaluate(**(kwargs | {'ruleset': canonical_bytes(rules)}))
    assert len({p['record_id'] for p in (first, dated, source_changed, rules_changed)}) == 4
    engine.repository.set_result(kwargs['input_id'], 'deepseek', None, 'failed')
    assert engine.load(first['record_id']) == first
    assert engine.load(rules_changed['record_id']) == rules_changed


def test_missing_sources_and_wrong_outcome_rejected(seeded):
    engine, kwargs = seeded
    with pytest.raises(ContextError, match='source bytes'):
        engine.evaluate(**(kwargs | {'rule_sources': []}))
    with pytest.raises(ContextError, match='source bytes'):
        engine.evaluate(**(kwargs | {'rule_sources': [RuleSource('x', b'a'), RuleSource('x', b'b')]}))
    ref = engine._put_json(make_outcome(), 'not-outcome')
    engine.repository.set_result(kwargs['input_id'], 'deepseek', ref['artifact_id'], 'completed')
    with pytest.raises(ContextError, match='not an outcome'):
        engine.evaluate(**kwargs)


def test_original_identity_and_file_mismatch_rejected(seeded):
    engine, kwargs = seeded
    row = engine.repository.get_input(kwargs['input_id'])
    engine.repository.register_input(row['batch_id'], row['relative_path'], content_hash='0' * 64)
    with pytest.raises(ContextError, match='original PDF'):
        engine.evaluate(**kwargs)
    original = engine.repository.original(row['object_key'])
    engine.repository.register_input(row['batch_id'], row['relative_path'], content_hash=original['sha256'])
    outcome = make_outcome(file_id='wrong')
    ref = engine._put_json(outcome, 'outcome')
    engine.repository.set_result(kwargs['input_id'], 'deepseek', ref['artifact_id'], 'completed')
    with pytest.raises(ContextError, match='file identity'):
        engine.evaluate(**kwargs)


def test_tampered_blob_rejected(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    ref = packet['sources']['ruleset']
    engine.storage.client.objects[engine.storage._url(ref['object_key'])] = b'tampered'
    with pytest.raises(ContextError, match='integrity'):
        engine.load(packet['record_id'])


def test_failed_upload_cannot_publish_record(seeded, monkeypatch):
    engine, kwargs = seeded
    saved = engine.storage.put
    saved_publish = engine.repository.publish
    published = []
    monkeypatch.setattr(engine.repository, 'publish', lambda values: published.append(values))

    def fail(data, kind, content_type):
        if kind == 'engine-evaluation':
            raise OSError('synthetic failure')
        return saved(data, kind, content_type)

    monkeypatch.setattr(engine.storage, 'put', fail)
    with pytest.raises(OSError):
        engine.evaluate(**kwargs)
    assert not published
    monkeypatch.setattr(engine.storage, 'put', saved)
    monkeypatch.setattr(engine.repository, 'publish', saved_publish)
    packet = engine.evaluate(**kwargs)
    assert engine.load(packet['record_id']) == packet


def test_missing_original_rejected(seeded, monkeypatch):
    engine, kwargs = seeded
    monkeypatch.setattr(engine.repository, 'original', lambda key: None)
    with pytest.raises(ContextError, match='original PDF artifact is missing'):
        engine.evaluate(**kwargs)


def test_index_tampering_rejected(seeded, monkeypatch):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    row = engine.repository.record(packet['record_id'])
    row['decision'] = 'NO_PAGAR' if row['decision'] != 'NO_PAGAR' else 'PAGAR'
    monkeypatch.setattr(engine.repository, 'record', lambda record_id: row)
    with pytest.raises(ContextError, match='identity mismatch'):
        engine.load(packet['record_id'])


@pytest.mark.asyncio
async def test_review_is_durable_separate_and_idempotent(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    before = engine.archive.get(packet['evaluation'])
    provider = ScriptedProvider()
    reviewed = await engine.review(packet['record_id'], provider=provider, request_key='review-1', reviewed_at=REVIEWED)
    assert provider.calls == 1
    assert reviewed == await engine.review(packet['record_id'], provider=provider, request_key='review-1', reviewed_at=REVIEWED)
    assert provider.calls == 1
    assert reviewed == engine.load(reviewed['record_id'])
    result = engine._json(reviewed['review'])
    assert result['status'] == 'INCOMPLETE'
    assert result['payment_authorized'] is False
    assert engine.archive.get(packet['evaluation']) == before
    assert reviewed['decision'] == packet['decision']
    assert engine._json(reviewed['provider_request'])['evaluation']['evaluation_id'] == packet['evaluation_id']
    with pytest.raises(ContextError, match='different inputs'):
        await engine.review(packet['record_id'], provider=provider, request_key='review-1', reviewed_at='2026-09-20T12:00:00Z')
    await engine.review(packet['record_id'], provider=provider, request_key='review-2', reviewed_at=REVIEWED)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_failed_review_persisted_without_changing_decision(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)

    class Invalid(ScriptedProvider):
        async def review(self, request):
            self.calls += 1
            return ProviderReply({}, self.model)

    provider = Invalid()
    reviewed = await engine.review(packet['record_id'], provider=provider, request_key='invalid', reviewed_at=REVIEWED)
    assert engine._json(reviewed['review'])['status'] == 'FAILED'
    assert engine._json(reviewed['review'])['error']['code'] == 'invalid_response'
    assert engine.load(reviewed['record_id']) == reviewed
    assert reviewed['decision'] == packet['decision']
    await engine.review(packet['record_id'], provider=provider, request_key='invalid', reviewed_at=REVIEWED)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_review_timeout_and_budget_are_archived(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)

    class Timeout(ScriptedProvider):
        async def review(self, request):
            raise TimeoutError()

    reviewed = await engine.review(packet['record_id'], provider=Timeout(), request_key='timeout', reviewed_at=REVIEWED)
    assert engine._json(reviewed['review'])['error']['code'] == 'timeout'
    assert engine.load(reviewed['record_id']) == reviewed
    provider = ScriptedProvider()
    oversized = await engine.review(packet['record_id'], provider=provider, request_key='budget',
                                    reviewed_at=REVIEWED, limits=ReviewLimits(max_input_bytes=1))
    assert provider.calls == 0
    assert engine._json(oversized['review'])['error']['code'] == 'input_too_large'
    assert engine.load(oversized['record_id']) == oversized


@pytest.mark.parametrize('kind,call_count', [('engine-review-request', 0), ('engine-review-response', 1)])
@pytest.mark.asyncio
async def test_review_archive_failure_blocks_same_key_retry(seeded, monkeypatch, kind, call_count):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    provider = ScriptedProvider()
    saved = engine.storage.put

    def fail(data, current_kind, content_type):
        if current_kind == kind:
            raise OSError('synthetic upload failure')
        return saved(data, current_kind, content_type)

    monkeypatch.setattr(engine.storage, 'put', fail)
    with pytest.raises(ArchiveError):
        await engine.review(packet['record_id'], provider=provider, request_key='archive-failure', reviewed_at=REVIEWED)
    assert provider.calls == call_count
    monkeypatch.setattr(engine.storage, 'put', saved)
    with pytest.raises(ContextError, match='in_progress_or_unknown'):
        await engine.review(packet['record_id'], provider=provider, request_key='archive-failure', reviewed_at=REVIEWED)
    assert provider.calls == call_count


def test_concurrent_review_claims_have_one_winner(seeded):
    engine, kwargs = seeded
    packet = engine.evaluate(**kwargs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: engine.repository.claim_review('concurrent', packet['record_id'], digest(b'request')), range(2)))
    assert sorted(acquired for _, acquired in claims) == [False, True]
    assert claims[0][0]['state'] == 'running'
    with pytest.raises(ContextError, match='different inputs'):
        engine.repository.claim_review('concurrent', packet['record_id'], digest(b'changed'))


def test_cli_evaluation_uses_durable_engine(seeded, tmp_path, monkeypatch, capsys):
    from rules_ingestion import engine_cli

    engine, kwargs = seeded
    rules = tmp_path / 'rules.json'
    rules.write_bytes(kwargs['ruleset'])
    sources = tmp_path / 'source.csv'
    sources.write_bytes(b'raw,csv\r\n')
    snapshots = tmp_path / 'snapshots.json'
    from dataclasses import asdict
    snapshots.write_text(json.dumps({key: asdict(value) for key, value in kwargs['snapshots'].items()}))
    monkeypatch.setattr(engine_cli.InvoiceDecisionEngine, 'from_supabase', lambda: engine)
    monkeypatch.setattr(engine, 'close', lambda: None)
    assert engine_cli.main(['evaluate', '--input-id', kwargs['input_id'], '--interpreter', 'deepseek',
                            '--ruleset', str(rules), '--rule-source', 'source.csv=' + str(sources),
                            '--snapshots', str(snapshots), '--evaluation-date', EVAL_DATE,
                            '--captured-at', CAPTURED]) == 0
    packet = json.loads(capsys.readouterr().out)
    assert engine.load(packet['record_id']) == packet
