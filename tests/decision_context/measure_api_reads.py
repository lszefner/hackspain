"""Opt-in before/after measurement against 99f2994; disposable local fixtures.

Run explicitly with pytest -s tests/decision_context/measure_api_reads.py.
Not part of default test discovery; never calls providers/shared databases.
"""
import json
import statistics
import subprocess
import time
import types

import pytest
from test_backend_engine import runtime as backend_runtime

from backend import run_revision as rr
from backend import server
from backend.flujo import flujo
from backend.results_store import PostgresResultsStore

runtime = backend_runtime
BASELINE = '99f2994'


def old_module(path, package):
    source = subprocess.check_output(['git', 'show', f'{BASELINE}:{path}'], text=True)
    module = types.ModuleType(package + '._measurement_baseline')
    module.__package__ = package
    module.__file__ = str(rr.ROOT / path)
    exec(compile(source, module.__file__, 'exec'), module.__dict__)  # noqa: S102 - trusted pinned repository baseline
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize('review', [False, True])
async def test_compare(runtime, monkeypatch, review):
    engine, kwargs, _, _, _ = runtime
    monkeypatch.setenv('REVISION_REVIEW_ENABLED', str(review).lower())
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    old_store_module = old_module('backend/results_store.py', 'backend')
    old_flow = old_module('backend/flujo.py', 'backend')
    old_server = old_module('backend/server.py', 'backend')
    old_repo = old_module('rules_ingestion/engine_storage.py', 'rules_ingestion')
    old_store = old_store_module.PostgresResultsStore(engine)
    new_store = PostgresResultsStore(repository=engine.repository)
    old_server.STORE = old_store
    old_server.FACTURAS_DIR = kwargs['input_dir']
    monkeypatch.setattr(server, 'STORE', new_store)
    monkeypatch.setattr(server, 'FACTURAS_DIR', kwargs['input_dir'])
    counts = {'db': 0, 'storage': 0}
    original_db = engine.repository._run
    original_get = engine.storage.client.get
    new_latest = engine.repository.latest_results

    def db(operation):
        counts['db'] += 1
        time.sleep(.01)
        return original_db(operation)

    def get(*args, **kw):
        counts['storage'] += 1
        time.sleep(.02)
        return original_get(*args, **kw)

    monkeypatch.setattr(engine.repository, '_run', db)
    monkeypatch.setattr(engine.storage.client, 'get', get)
    results = {}
    for name, old, new in (
        ('summary', old_server._resumen, server._resumen),
        ('detail', lambda: old_store.get('invoice.pdf'), lambda: new_store.get('invoice.pdf')),
        ('flow', lambda: old_flow.flujo(old_store, 'invoice.pdf'), lambda: flujo(new_store, 'invoice.pdf')),
        ('run', lambda: rr.run_status(engine, kwargs['request_key']), lambda: new_store.run_status(kwargs['request_key'])),
    ):
        results[name] = {}
        bodies = {}
        for mode, read in [('before', old), ('after', new)]:
            monkeypatch.setattr(engine.repository, 'latest_results',
                old_repo.EngineRepository.latest_results.__get__(engine.repository) if mode == 'before' else new_latest)
            samples = []
            for _ in range(3):
                counts.update(db=0, storage=0)
                started = time.monotonic()
                bodies[mode] = read()
                samples.append(round((time.monotonic() - started) * 1000, 2))
            results[name][mode] = {**counts, 'median_ms': statistics.median(samples), 'samples_ms': samples}
        if name == 'detail':
            for key in ('decision', 'evaluation_result', 'contextual_review', 'checks', 'raw_invoice'):
                assert bodies['before'][key] == bodies['after'][key]
        else:
            assert bodies['before'] == bodies['after']
    print('\nMEASUREMENT ' + json.dumps({'baseline': BASELINE, 'review': review, 'results': results}, sort_keys=True))
