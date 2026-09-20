from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_decision_context import EVAL_DATE, make_outcome, make_ruleset
from test_decision_integration import _workbook
from test_decision_storage import _Client

from backend import run_revision as rr
from backend.results_store import PostgresResultsStore, ResultsStore
from ingestion.contracts import canonical_bytes, digest
from ingestion.pipeline import Pipeline, StageError
from ingestion.storage import SupabaseStorage
from rules_ingestion.contextual_review import ProviderReply
from rules_ingestion.decision_context import ContextError
from rules_ingestion.engine import InvoiceDecisionEngine, RuleSource
from rules_ingestion.engine_storage import EngineRepository
from tests.test_core_engine_review import ScriptedProvider


@pytest.fixture
def runtime(db, tmp_path, monkeypatch):
    from rules_ingestion import build_rules

    repository = EngineRepository(db.dsn)
    storage = SupabaseStorage(url='https://storage.invalid', key='test-only', client=_Client())
    engine = InvoiceDecisionEngine(repository, storage)
    source_yaml, workbook = _workbook(tmp_path / 'sources')
    pdfs = tmp_path / 'pdfs'
    pdfs.mkdir()
    (pdfs / 'invoice.pdf').write_bytes(b'%PDF-test')
    rules = tmp_path / 'rules.json'
    rules.write_bytes(canonical_bytes(make_ruleset()))
    calls = {'read': 0, 'interpret': 0, 'generate': 0}
    monkeypatch.setattr(rr, 'credentials', lambda config: {'HELMCODE_API_KEY': 'synthetic'})
    monkeypatch.setattr(rr.ErpClient, 'snapshot', lambda self: {'records': [], 'pages': [], 'complete': False})
    monkeypatch.setenv('JEV_API_KEY', 'synthetic')
    monkeypatch.setenv('HELMCODE_API_KEY', 'synthetic')
    for key in ('REVISION_RULESET_PATH', 'REVISION_BACKEND'):
        monkeypatch.delenv(key, raising=False)

    def generate(loaded, profile_bytes, *, generated_at):
        calls['generate'] += 1
        assert loaded.workbook_bytes == workbook.read_bytes()
        assert profile_bytes
        return canonical_bytes(make_ruleset()), {'classifications': [], 'workbook_sha256': digest(loaded.workbook_bytes)}

    monkeypatch.setattr(build_rules, 'build_frozen_rules', generate)

    async def read(self, batch, entry, item):
        calls['read'] += 1
        assert entry['object_key']
        original = self.storage.get(entry['object_key'])
        assert digest(original) == item['source_sha256']
        render = self.bytes_artifact(b'fake-page', 'render', 'image/png')

        async def operation(attempt):
            raw = self.bytes_artifact(b'raw OCR response', 'provider-response', 'application/octet-stream')
            self.repo.record_attempt_artifact(attempt['id'], raw['id'])
            return {'reading': make_outcome(file_id=item['file_id'])['reading'],
                    'layout': {'pages': [{'image_artifact_id': str(render['id'])}]},
                    'raw': {'ocr': [{'text': 'raw invoice text'}]}}

        return await self.job(batch, entry, 'reading', item['source_sha256'], 'scripted', 'test', operation)

    async def interpret(self, batch, entry, reading):
        calls['interpret'] += 1

        async def operation(attempt):
            outcome = make_outcome(file_id=reading['file_id'])
            return {'invoice': outcome['invoice'], 'evidence': outcome['evidence'],
                    'checks': {}, 'raw': {'response': 'raw structured output'}, 'status': 'completed'}

        return await self.job(batch, entry, 'interpretation', digest(canonical_bytes(reading)), 'scripted', 'test', operation)

    monkeypatch.setattr(Pipeline, 'read', read)
    monkeypatch.setattr(Pipeline, 'interpret', interpret)
    kwargs = {'request_key': 'intent-1', 'evaluation_date': EVAL_DATE,
              'input_dir': pdfs, 'sources_yaml': str(source_yaml), 'engine': engine,
              'review_provider': ScriptedProvider()}
    yield engine, kwargs, calls, rules, workbook
    repository.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('second_name', ['invoice.pdf', 'renamed.pdf'])
async def test_resubmission_rejected_even_with_same_filename(runtime, monkeypatch, second_name):
    from test_decision_context import make_snapshots

    from tests.test_rule_execution import rule

    engine, kwargs, calls, rules, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    # Supply an unambiguous supplier and complete ERP history so the first
    # submission can pass and the second fails specifically as a duplicate.
    monkeypatch.setattr(rr, 'capture_master_snapshots', lambda *a, **k: make_snapshots(histories=False))
    monkeypatch.setattr(rr.ErpClient, 'snapshot', lambda self: {
        'records': [{'pedido': 'PO-1', 'estado': 'PENDIENTE'}], 'pages': [], 'complete': True})
    rules.write_bytes(canonical_bytes(make_ruleset([rule('DUPLICATES')])))
    kwargs.update(ruleset_path=str(rules), rule_sources=[RuleSource('rules.csv', b'original,rule\n')])
    first = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert first['files'][0]['decision'] == 'PAGAR'
    first_id = first['files'][0]['evaluation_record_id']
    original_packet = engine.load(first_id)
    original_evaluation = engine._json(original_packet['evaluation'])
    # Same intent is a read of the saved run, not a new submission.
    assert await rr.revisar_lote(['invoice.pdf'], **kwargs) == first
    assert calls['interpret'] == 1
    Path(kwargs['input_dir'], second_name).write_bytes(b'%PDF-test')
    kwargs['request_key'] = 'resubmission'
    second = await rr.revisar_lote([second_name], **kwargs)
    assert second['state'] == 'completed'
    assert second['files'][0]['decision'] == 'NO_PAGAR'
    stored = PostgresResultsStore(engine).get(second_name)
    assert {r['code'] for r in stored['evaluation_result']['decision_reasons']} == {'DUPLICATE_SUBMISSION'}
    context = json.loads(stored['decision_context'])
    assert context['history_observations']['processed']['hard_matches']
    packet = engine.load(second['files'][0]['evaluation_record_id'])
    history = engine._json(packet['sources']['processed'])
    assert history['same_file_policy'] == 'include'
    assert history['records'][0]['context_id'] == engine._json(original_packet['context'])['context_id']
    assert engine._json(engine.load(first_id)['evaluation']) == original_evaluation


@pytest.mark.asyncio
async def test_existing_backend_runs_full_latest_engine(runtime):
    engine, kwargs, calls, _rules, workbook = runtime
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert result['revision_counts'] == {'stored': 1, 'failed': 0}
    assert calls == {'read': 1, 'interpret': 1, 'generate': 1}
    assert kwargs['review_provider'].calls == 1
    row = PostgresResultsStore(engine).get('invoice.pdf')
    assert row['estado'] == 'hecha'
    assert row['evaluation_result']['context_schema_version'] == 'decision-context/2'
    assert row['contextual_review']['status'] == 'INCOMPLETE'
    assert row['contextual_review']['payment_authorized'] is False
    assert row['decision'] == row['evaluation_result']['preliminary_decision']
    run = engine.repository.get_run('intent-1')
    frozen = engine._json(engine.archive.ref(str(run['input_artifact_id'])))
    assert frozen['rule_generation']['kind'] == 'engine-rule-generation'
    assert engine.archive.get(frozen['snapshots']['workbook']['payload_artifact']) == workbook.read_bytes()
    assert rr.run_status(engine, 'intent-1') == result
    assert await rr.revisar_lote(['invoice.pdf'], **kwargs) == result
    assert calls == {'read': 1, 'interpret': 1, 'generate': 1}
    assert kwargs['review_provider'].calls == 1
    Path(kwargs['input_dir'], 'invoice.pdf').unlink()
    assert PostgresResultsStore(engine).get('invoice.pdf')['evaluation_result'] == row['evaluation_result']
    assert rr.run_status(engine, 'intent-1') == result


@pytest.mark.asyncio
async def test_pinned_rules_still_run_latest_evaluator_and_ai_review(runtime):
    engine, kwargs, calls, rules, _workbook_path = runtime
    kwargs.update(ruleset_path=str(rules), rule_sources=[RuleSource('rules.csv', b'original,rule\n')])
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert calls['generate'] == 0
    assert kwargs['review_provider'].calls == 1
    assert engine.load(result['files'][0]['evaluation_record_id'])['kind'] == 'evaluation'
    assert engine.load(result['files'][0]['review_record_id'])['kind'] == 'review'
    Path(kwargs['input_dir'], 'invoice.pdf').write_bytes(b'%PDF-changed')
    with pytest.raises(ContextError, match='different inputs'):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert calls['read'] == 1


@pytest.mark.asyncio
async def test_failed_review_stays_failed_and_does_not_repeat(runtime):
    engine, kwargs, calls, _rules, _workbook_path = runtime

    class Invalid(ScriptedProvider):
        async def review(self, request):
            self.calls += 1
            return ProviderReply({}, self.model)

    kwargs['review_provider'] = Invalid()
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'failed'
    assert result['files'][0]['review_status'] == 'FAILED'
    assert result['files'][0]['evaluation_record_id']
    assert PostgresResultsStore(engine).get('invoice.pdf')['estado'] == 'error'
    assert await rr.revisar_lote(['invoice.pdf'], **kwargs) == result
    assert calls['read'] == 1
    assert kwargs['review_provider'].calls == 1


@pytest.mark.asyncio
async def test_extraction_failure_is_persisted_without_evaluation(runtime, monkeypatch):
    engine, kwargs, _calls, _rules, _workbook_path = runtime

    async def fail(*args):
        raise StageError('synthetic_read_error')

    monkeypatch.setattr(Pipeline, 'read', fail)
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['revision_counts'] == {'stored': 0, 'failed': 1}
    assert result['files'][0]['evaluation_record_id'] is None
    assert kwargs['review_provider'].calls == 0
    assert PostgresResultsStore(engine).get('invoice.pdf')['decision'] is None
    assert rr.run_status(engine, 'intent-1') == result


@pytest.mark.asyncio
async def test_storage_failure_leaves_unknown_durable_intent(runtime, monkeypatch):
    engine, kwargs, calls, _rules, _workbook_path = runtime
    saved = engine.storage.put

    def fail(data, kind, content_type):
        if kind == 'engine-run-result':
            raise OSError('synthetic persistence outage')
        return saved(data, kind, content_type)

    monkeypatch.setattr(engine.storage, 'put', fail)
    with pytest.raises(OSError):
        await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert rr.run_status(engine, 'intent-1')['state'] == 'unknown'
    monkeypatch.setattr(engine.storage, 'put', saved)
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'unknown'
    assert calls['read'] == 1
    assert kwargs['review_provider'].calls == 1


@pytest.mark.asyncio
async def test_local_backend_and_sqlite_rejected(runtime, tmp_path):
    _engine, kwargs, calls, _rules, _workbook_path = runtime
    with pytest.raises(ValueError, match='Supabase'):
        await rr.revisar_lote(['invoice.pdf'], backend='local', **kwargs)
    with pytest.raises(ValueError, match='PostgresResultsStore'):
        await rr.revisar_lote(['invoice.pdf'], store=ResultsStore(tmp_path / 'legacy.db'), **kwargs)
    assert calls == {'read': 0, 'interpret': 0, 'generate': 0}


@pytest.mark.asyncio
async def test_missing_result_and_corrupt_outcome_cannot_approve(runtime, monkeypatch):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    original = engine.repository.results
    monkeypatch.setattr(engine.repository, 'results', lambda batch_id: [])
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'failed'
    assert kwargs['review_provider'].calls == 0
    monkeypatch.setattr(engine.repository, 'results', original)
    kwargs['request_key'] = 'intent-2'
    evaluate = engine.evaluate

    def corrupted(**values):
        row = engine.repository.extraction(values['input_id'], values['interpreter'])
        ref = engine.archive.ref(str(row['outcome_artifact_id']))
        engine.repository._query(
            "UPDATE ingestion.artifacts SET payload = '{}'::jsonb WHERE id = %s RETURNING id",
            (ref['artifact_id'],))
        return evaluate(**values)

    monkeypatch.setattr(engine, 'evaluate', corrupted)
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'failed'
    assert kwargs['review_provider'].calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['duplicate', 'unexpected'])
async def test_backend_result_accounting(runtime, monkeypatch, mode):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    original = engine.repository.results

    def rows(batch_id):
        values = original(batch_id)
        return values + ([values[0]] if mode == 'duplicate' else [values[0] | {'file_name': 'ghost.pdf'}])

    monkeypatch.setattr(engine.repository, 'results', rows)
    if mode == 'unexpected':
        with pytest.raises(ContextError, match='unexpected extraction'):
            await rr.revisar_lote(['invoice.pdf'], **kwargs)
        assert rr.run_status(engine, 'intent-1')['state'] == 'unknown'
    else:
        result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
        assert result['revision_counts'] == {'stored': 0, 'failed': 1}
    assert kwargs['review_provider'].calls == 0


@pytest.mark.asyncio
async def test_one_failed_document_does_not_hide_other_results(runtime, monkeypatch):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    Path(kwargs['input_dir'], 'bad.pdf').write_bytes(b'%PDF-failure')
    original = Pipeline.read

    async def read(self, batch, entry, item):
        if item['file_id'] == 'bad.pdf':
            raise StageError('synthetic_failure')
        return await original(self, batch, entry, item)

    monkeypatch.setattr(Pipeline, 'read', read)
    result = await rr.revisar_lote(['invoice.pdf', 'bad.pdf'], **kwargs)
    assert result['state'] == 'partial'
    assert result['revision_counts'] == {'stored': 1, 'failed': 1}
    with pytest.raises(ContextError, match='different inputs'):
        await rr.revisar_lote(['bad.pdf', 'invoice.pdf'], **kwargs)
    store = PostgresResultsStore(engine)
    assert store.get('invoice.pdf')['review_record_id']
    assert store.get('bad.pdf')['decision'] is None
    history = store.processed_history_snapshot(captured_at='2026-09-19T12:00:00Z')
    assert len(history.payload['records']) == 1
    assert history.payload['records'][0]['file_id'] == 'invoice.pdf'
    assert history.payload['complete'] is True
    assert store.processed_history_snapshot(captured_at='2026-09-19T12:00:00Z', exclude_file_id='invoice.pdf').payload['records'] == []


@pytest.mark.asyncio
async def test_processed_history_reads_projection_without_forensic_replay(runtime, monkeypatch):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    store = PostgresResultsStore(engine)
    gets = {'count': 0}
    original_get = engine.storage.get

    def counted_get(object_key):
        gets['count'] += 1
        return original_get(object_key)

    monkeypatch.setattr(engine.storage, 'get', counted_get)
    monkeypatch.setattr(engine, 'load', lambda record_id: pytest.fail('forensic replay on history path'))
    history = store.processed_history_snapshot(captured_at='2026-09-19T12:00:00Z')
    records = history.payload['records']
    assert len(records) == 1
    assert records[0]['file_id'] == 'invoice.pdf'
    assert records[0]['context_id'].startswith('dc_')
    assert set(records[0]) == {'file_id', 'context_id', 'invoice_number', 'supplier_id', 'total', 'currency', 'issue_date'}
    assert gets['count'] == 0
    gets['count'] = 0
    again = store.processed_history_snapshot(captured_at='2026-09-19T12:00:00Z')
    assert gets['count'] == 0
    assert again.payload == history.payload
    assert store.processed_history_snapshot(captured_at='2026-09-19T12:00:00Z', exclude_file_id='invoice.pdf').payload['records'] == []


def test_backend_import_is_lazy_and_request_key_is_required(monkeypatch):
    from backend import server

    assert server.STORE is None
    monkeypatch.setattr(server, '_procesando', False)
    handler = object.__new__(server.Handler)
    with pytest.raises(ValueError, match='request_key'):
        handler._lanzar({'objetivo': ['una'], 'file_id': ['invoice.pdf']})
    assert server._procesando is False


@pytest.mark.asyncio
async def test_backend_read_api_uses_database_records(runtime, monkeypatch):
    from backend import server

    engine, kwargs, _calls, _rules, _workbook_path = runtime
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    monkeypatch.setattr(server, 'STORE', PostgresResultsStore(engine))
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._factura('invoice.pdf')
    assert responses[0][0] == 200
    assert responses[0][1]['evaluation_record_id'] == result['files'][0]['evaluation_record_id']
    assert responses[0][1]['contextual_review']['payment_authorized'] is False
    assert responses[0][1]['evaluation_result']['context_schema_version'] == 'decision-context/2'


@pytest.mark.asyncio
async def test_flujo_endpoint_exposes_full_pipeline(runtime, monkeypatch):
    from backend import server
    from backend.export_outcomes import decide_output

    engine, kwargs, _calls, _rules, _workbook_path = runtime
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    store = PostgresResultsStore(engine)
    monkeypatch.setattr(server, 'STORE', store)
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._flujo('invoice.pdf')
    status, body = responses[0]
    assert status == 200
    json.dumps(body)
    extraccion = body['extraccion']
    assert extraccion['ruta'] == 'vision'
    assert extraccion['determinista'] == {'attempted': False, 'accepted': False,
                                        'gaps': ['no_text_layer_or_cascade_off']}
    assert extraccion['por_que_vision'] == ['no_text_layer_or_cascade_off']
    assert [t['stage'] for t in extraccion['trabajos']] == ['reading', 'interpretation']
    for trabajo in extraccion['trabajos']:
        assert trabajo['provider'] == 'scripted'
        assert trabajo['model'] == 'test'
        assert trabajo['state'] == 'succeeded'
        assert 'config' not in trabajo and 'settings' not in trabajo
        assert len(trabajo['intentos']) == 1
        assert trabajo['intentos'][0]['status'] == 'succeeded'
    assert len(extraccion['trabajos'][0]['intentos'][0]['raw_artifact_ids']) == 1
    assert extraccion['factura']['file_id'] == 'invoice.pdf'
    assert body['evaluacion']['record_id'] == result['files'][0]['evaluation_record_id']
    assert body['evaluacion']['resultado']['preliminary_decision'] == body['evaluacion']['decision']
    assert body['revision']['status'] == 'INCOMPLETE'
    assert body['revision']['record_id'] == result['files'][0]['review_record_id']
    assert body['ejecucion']['request_key'] == 'intent-1'
    assert body['ejecucion']['state'] == 'completed'
    assert body['ejecucion']['rule_generation'] == 'generated'
    assert 'workbook' in body['ejecucion']['snapshots']
    assert all('payload_artifact' not in s for s in body['ejecucion']['snapshots'].values())
    assert body['salida'] == dict(zip(('verdict', 'basis'), decide_output(store.get('invoice.pdf'))))
    assert body['resolucion'] is None and body['pago'] is None
    assert [e['etapa'] for e in body['etapas']] == \
        ['recibida', 'extraida', 'evaluada', 'revisada', 'emitida', 'resuelta', 'pagada']
    etapas = {e['etapa']: e for e in body['etapas']}
    assert etapas['resuelta']['estado'] == 'no_registrada'
    assert etapas['pagada']['estado'] == 'no_registrada'
    assert etapas['evaluada']['estado'] == 'hecha'
    assert etapas['revisada']['estado'] == 'hecha'


@pytest.mark.asyncio
async def test_flujo_extraction_failure_shows_failed_stage(runtime, monkeypatch):
    from backend import server

    engine, kwargs, _calls, _rules, _workbook_path = runtime

    async def fail(*args):
        raise StageError('synthetic_read_error')

    monkeypatch.setattr(Pipeline, 'read', fail)
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    monkeypatch.setattr(server, 'STORE', PostgresResultsStore(engine))
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._flujo('invoice.pdf')
    status, body = responses[0]
    assert status == 200
    assert body['extraccion']['status'] == 'failed'
    assert body['extraccion']['error']['code'] == 'synthetic_read_error'
    assert body['extraccion']['trabajos'] == []
    assert body['evaluacion'] is None
    assert body['revision'] is None
    assert body['salida'] == {'verdict': 'ESCALAR', 'basis': 'no_evaluation'}
    etapas = {e['etapa']: e for e in body['etapas']}
    assert etapas['extraida']['estado'] == 'error'
    assert etapas['evaluada']['estado'] == 'pendiente'


def test_salud_reports_dependency_state(runtime, monkeypatch):
    from backend import server

    engine, _kwargs, _calls, _rules, _workbook_path = runtime
    monkeypatch.setattr(server, 'STORE', PostgresResultsStore(engine))
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._salud()
    status, body = responses[-1]
    assert status == 200
    assert body['postgres'] is True

    def fail():
        raise RuntimeError('synthetic_store_outage')

    monkeypatch.setattr(server, 'get_store', fail)
    handler._salud()
    status, body = responses[-1]
    assert status == 200
    assert body['ok'] is False
    assert body['error'] == 'RuntimeError'


@pytest.mark.asyncio
async def test_flujo_pending_and_missing(runtime, monkeypatch):
    from backend import server

    engine, kwargs, _calls, _rules, _workbook_path = runtime
    monkeypatch.setattr(server, 'STORE', PostgresResultsStore(engine))
    monkeypatch.setattr(server, 'FACTURAS_DIR', kwargs['input_dir'])
    handler = object.__new__(server.Handler)
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._flujo('invoice.pdf')
    status, body = responses[-1]
    assert status == 200
    assert body['identidad'] is None
    assert body['extraccion'] is None
    assert body['salida'] == {'verdict': 'ESCALAR', 'basis': 'not_processed'}
    etapas = {e['etapa']: e for e in body['etapas']}
    assert etapas['recibida']['estado'] == 'pendiente'
    handler._flujo('nope.pdf')
    assert responses[-1][0] == 404
    handler._flujo('../x.pdf')
    assert responses[-1][0] == 422


@pytest.mark.asyncio
async def test_issue_date_policy_evaluates_on_each_invoice_date(runtime):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    kwargs['evaluation_date'] = 'issue-date'
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert result['files'][0]['evaluation_date'] == '2026-09-01'
    packet = engine.load(result['files'][0]['evaluation_record_id'])
    assert engine._json(packet['evaluation'])['evaluation_date'] == '2026-09-01'
    run = engine.repository.get_run('intent-1')
    frozen = engine._json(engine.archive.ref(str(run['input_artifact_id'])))
    assert frozen['evaluation_date_policy'] == 'issue-date'
    assert frozen['signature']['evaluation_date'] == 'issue-date'


@pytest.mark.asyncio
async def test_issue_date_policy_falls_back_when_unreadable(runtime, monkeypatch):
    engine, kwargs, _calls, _rules, _workbook_path = runtime
    kwargs['evaluation_date'] = 'issue-date'

    async def interpret(self, batch, entry, reading):
        async def operation(attempt):
            outcome = make_outcome(file_id=reading['file_id'])
            outcome['invoice']['issue_date'] = None
            return {'invoice': outcome['invoice'], 'evidence': outcome['evidence'],
                    'checks': {}, 'raw': {'response': 'raw structured output'}, 'status': 'completed'}

        return await self.job(batch, entry, 'interpretation',
                              digest(canonical_bytes(reading)), 'scripted', 'test', operation)

    monkeypatch.setattr(Pipeline, 'interpret', interpret)
    result = await rr.revisar_lote(['invoice.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert result['files'][0]['evaluation_date_fallback'] is True
    assert result['files'][0]['evaluation_date'][:4].isdigit()
    packet = engine.load(result['files'][0]['evaluation_record_id'])
    assert engine._json(packet['evaluation'])['evaluation_date'] == \
        result['files'][0]['evaluation_date']


@pytest.mark.asyncio
async def test_reviews_run_for_every_evaluated_file_in_order(runtime):
    _engine, kwargs, _calls, _rules, _workbook_path = runtime
    pdfs = Path(kwargs['input_dir'])
    (pdfs / 'a.pdf').write_bytes(b'%PDF-a')
    (pdfs / 'b.pdf').write_bytes(b'%PDF-b')
    result = await rr.revisar_lote(['invoice.pdf', 'a.pdf', 'b.pdf'], **kwargs)
    assert result['state'] == 'completed'
    assert [row['file_id'] for row in result['files']] == ['invoice.pdf', 'a.pdf', 'b.pdf']
    assert kwargs['review_provider'].calls == 3
    for row in result['files']:
        assert row['review_record_id'] and row['review_status'] == 'INCOMPLETE'


def test_backend_uses_latest_caja_resolver():
    import os

    from backend import caja_paths

    assert rr.FACTURAS_DIR == Path(os.environ.get('REVISION_INPUT_DIR') or caja_paths.facturas())


def test_engine_path_does_not_import_the_legacy_alberto_package():
    # The engine must keep running once the alberto track is deleted.
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = [
        str(path.relative_to(root))
        for folder in ('backend', 'ingestion', 'rules_ingestion')
        for path in (root / folder).rglob('*.py')
        if 'from alberto' in path.read_text() or 'import alberto' in path.read_text()
    ]
    assert offenders == []


def test_rule_builder_reuses_existing_ai_stages(monkeypatch):
    from rules_ingestion import build_rules, codegen, merge, store
    from rules_ingestion.loader import LoadResult

    calls = []
    loaded = LoadResult(norma_lines=[{'text': 'synthetic source'}])
    monkeypatch.setattr(build_rules, 'classify_lines', lambda rows, **kw: calls.append(('classify', rows, kw)) or [])
    monkeypatch.setattr(store, 'build_ruleset', lambda *args, **kw: {'rules': []})
    monkeypatch.setattr(merge, 'build_merged', lambda doc, discovered: make_ruleset())
    monkeypatch.setattr(codegen, 'enrich_new_rules', lambda doc, **kw: calls.append(('compile', kw)))
    result, audit = build_rules.build_frozen_rules(loaded, b'policy_id: test', generated_at='2026-09-19T12:00:00Z')
    assert json.loads(result) == make_ruleset()
    assert calls[0] == ('classify', loaded.norma_lines, {'prefer_jev': True, 'use_llm': True})
    assert calls[1] == ('compile', {'use_llm': True, 'gen_python': False})
    assert audit['discovery_cache_used'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('file_count', [1, 3])
async def test_correction_reuses_submission_preserves_audit_and_projects_new_run(runtime, monkeypatch, file_count):
    from test_decision_context import make_snapshots

    from backend import correct_revision
    from backend.audit_reader import AuditReader
    from tests.test_rule_execution import rule

    engine, kwargs, calls, rules, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', 'false')
    monkeypatch.setattr(rr, 'capture_master_snapshots', lambda *a, **k: make_snapshots(histories=False))
    monkeypatch.setattr(rr.ErpClient, 'snapshot', lambda self: {
        'records': [{'pedido': 'PO-1', 'estado': 'PENDIENTE'}], 'pages': [], 'complete': True})
    monkeypatch.setattr(correct_revision, 'capture_master_snapshots',
                        lambda *a, **k: make_snapshots(histories=False))
    rules.write_bytes(canonical_bytes(make_ruleset([rule('DUPLICATES')])))
    sources = [RuleSource('rules.csv', b'original,rule\n')]
    kwargs.update(ruleset_path=str(rules), rule_sources=sources)
    file_ids = ['invoice.pdf', 'second.pdf', 'third.pdf'][:file_count]
    for name in file_ids[1:]:
        Path(kwargs['input_dir'], name).write_bytes(b'%PDF-test')
    original = await rr.revisar_lote(file_ids, **kwargs)
    old_id = original['files'][0]['evaluation_record_id']
    reader = AuditReader(engine.repository)
    old_packet = reader.packet(old_id)
    call_args = {'request_key': 'correction-1', 'ruleset': rules.read_bytes(), 'rule_sources': sources,
                 'sources_yaml': kwargs['sources_yaml'], 'evaluation_date': EVAL_DATE,
                 'input_dir': kwargs['input_dir']}
    result = correct_revision.correct_existing(engine, file_ids, **call_args)
    assert result['state'] == 'completed'
    assert result['files'][0]['decision'] == ('PAGAR' if file_count == 1 else 'NO_PAGAR')
    assert [r['file_id'] for r in result['files']] == file_ids
    assert calls['interpret'] == file_count
    assert reader.packet(old_id) == old_packet
    latest = PostgresResultsStore(engine).get('invoice.pdf')
    assert latest['request_key'] == 'correction-1'
    assert latest['review_status'] == 'DISABLED'
    assert latest['evaluation_record_id'] != old_id
    assert correct_revision.correct_existing(engine, file_ids, **call_args) == result


@pytest.mark.asyncio
async def test_raw_provider_error_bytes_survive_compact_json_mode(runtime):
    engine, _kwargs, _calls, _rules, _workbook = runtime
    engine.storage.compact_json = True
    pipeline = Pipeline(engine.repository, engine.storage, None, {})
    for content in (b'', b'<html>provider error</html>', b'{"error":"unavailable"}'):
        artifact = pipeline.bytes_artifact(content, 'provider-response', 'application/json')
        assert not artifact['object_key'].startswith('postgres/')
        assert engine.storage.get(artifact['object_key']) == content
