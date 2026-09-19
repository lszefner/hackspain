"""Read-only projection of one invoice's full processing flow.

Everything here comes from persisted Postgres rows and archived artifacts:
the endpoint never recomputes rules and never calls a provider. A missing or
corrupt artifact degrades its own section to an ``error`` entry instead of
failing the whole response.
"""
from __future__ import annotations

import json

from backend.export_outcomes import decide_output
from rules_ingestion.decision_context import ContextError
from rules_ingestion.decision_storage import _jsonable
from rules_ingestion.engine_storage import ArchiveError

_LOAD_ERRORS = (ContextError, ArchiveError, KeyError, ValueError, TypeError,
                OSError, json.JSONDecodeError)

_JOB_KEYS = ('stage', 'provider', 'model', 'state', 'attempt_count',
             'max_attempts', 'config_version', 'prompt_version',
             'adapter_version', 'schema_hash', 'last_error', 'created_at')
_ATTEMPT_KEYS = ('attempt_number', 'status', 'started_at', 'finished_at',
                 'latency_seconds', 'usage', 'provider_request_id', 'error',
                 'raw_artifact_ids')
_SOURCE_KEYS = ('kind', 'sha256', 'artifact_ref', 'captured_at', 'as_of',
                'asserted_by', 'authoritative_for', 'scope', 'availability')


def _load(fn):
    """Return (value, None) or (None, {"code": ...}) for a stored artifact."""
    try:
        return fn(), None
    except _LOAD_ERRORS as exc:
        return None, {'code': getattr(exc, 'code', None) or type(exc).__name__}


def _etapa(etapa, estado, at=None, ref=None):
    return {'etapa': etapa, 'estado': estado, 'at': at, 'ref': ref}


def _trabajos(repo, row):
    jobs, error = _load(lambda: repo.list_jobs(row['batch_id']))
    if error:
        return {'error': error}
    trabajos = []
    for job in jobs or []:
        if str(job.get('input_id')) != str(row['input_id']):
            continue
        attempts, _ = _load(lambda job=job: repo.list_attempts(job['id']))
        trabajo = {key: _jsonable(job.get(key)) for key in _JOB_KEYS}
        trabajo['id'] = _jsonable(job.get('id'))
        trabajo['artifact_id'] = _jsonable(job.get('artifact_id'))
        trabajo['extra'] = _jsonable((job.get('settings') or {}).get('extra'))
        trabajo['intentos'] = [
            {key: _jsonable(attempt.get(key)) for key in _ATTEMPT_KEYS}
            for attempt in attempts or []]
        trabajos.append(trabajo)
    return trabajos


def _ejecucion(engine, repo, row):
    request_key = row.get('request_key')
    if not request_key:
        return None
    ejecucion = {'request_key': request_key, 'state': row.get('run_state'),
                 'error': row.get('run_error'), 'captured_at': None,
                 'evaluation_date_policy': None, 'rule_generation': None,
                 'snapshots': None}
    run, error = _load(lambda: repo.get_run(request_key))
    if error:
        ejecucion['artifact_error'] = error
        return ejecucion
    if not run or not run.get('input_artifact_id'):
        return ejecucion
    frozen, error = _load(lambda: engine._json(
        engine.archive.ref(str(run['input_artifact_id']))))
    if error:
        ejecucion['artifact_error'] = error
        return ejecucion
    ejecucion['captured_at'] = frozen.get('captured_at')
    ejecucion['evaluation_date_policy'] = frozen.get('evaluation_date_policy')
    generation = frozen.get('rule_generation')
    ejecucion['rule_generation'] = (
        generation if generation in ('pinned', 'pending') else 'generated')
    ejecucion['snapshots'] = {
        name: {key: _jsonable(value) for key, value in meta.items()
               if key != 'payload_artifact'}
        for name, meta in (frozen.get('snapshots') or {}).items()}
    return ejecucion


def _extraccion(engine, repo, row):
    if row.get('outcome_artifact_id'):
        outcome, error = _load(lambda: engine._json(engine.archive.ref(
            str(row['outcome_artifact_id']))))
    else:
        outcome, error = None, None
    outcome = outcome or {}
    extraction = outcome.get('extraction') or {}
    determinista = extraction.get('deterministic')
    ruta = extraction.get('route')
    layout = outcome.get('layout') or {}
    pages = layout.get('pages')
    return {
        'status': outcome.get('status') or row.get('extraction_status'),
        'error': outcome.get('error') or row.get('extraction_error') or error,
        'cost_usd': outcome.get('cost_usd'),
        'ruta': ruta,
        'determinista': _jsonable(determinista),
        'por_que_vision': list(determinista.get('gaps') or [])
        if ruta == 'vision' and determinista else [],
        'paginas': len(pages) if isinstance(pages, list) else None,
        'trabajos': _trabajos(repo, row),
        'factura': _jsonable(outcome.get('invoice')),
        'checks': _jsonable(outcome.get('checks')),
        'gaps': _jsonable(outcome.get('gaps') or []),
    }


def _evaluacion(engine, row):
    record_id = row.get('evaluation_record_id')
    if not record_id:
        return None
    evaluacion = {'record_id': record_id}
    packet, error = _load(lambda: engine.load(record_id))
    if error:
        evaluacion['error'] = error
        return evaluacion
    resultado, error = _load(lambda: engine._json(packet['evaluation']))
    context, context_error = _load(lambda: engine._json(packet['context']))
    evaluacion.update({
        'evaluation_id': packet.get('evaluation_id'),
        'evaluation_date': packet.get('evaluation_date'),
        'captured_at': packet.get('captured_at'),
        'interpreter': packet.get('interpreter'),
        'decision': packet.get('decision'),
        'resultado': _jsonable(resultado) if resultado is not None else error,
        'fuentes': {
            name: {key: _jsonable(source.get(key)) for key in _SOURCE_KEYS
                   if key in source}
            for name, source in ((context or {}).get('sources') or {}).items()}
        if context is not None else context_error,
        'rule_source_lineage': _jsonable(packet.get('rule_source_lineage')),
    })
    return evaluacion


def _revision(engine, row):
    record_id = row.get('review_record_id')
    if not record_id:
        return None
    revision = {'record_id': record_id}
    packet, error = _load(lambda: engine.load(record_id))
    if error:
        revision['error'] = error
        return revision
    review, error = _load(lambda: engine._json(packet['review']))
    config, _ = _load(lambda: engine._json(packet['review_config']))
    review = review or {}
    revision.update({
        'status': review.get('status'),
        'attention_required': review.get('attention_required'),
        'error': review.get('error') or error,
        'reviewed_at': review.get('reviewed_at') or packet.get('reviewed_at'),
        'rule_reviews': _jsonable(review.get('rule_reviews')),
        'findings': _jsonable(review.get('findings')),
        'provider': (config or {}).get('provider'),
        'model': (config or {}).get('model'),
    })
    return revision


def flujo(store, file_id: str, en_disco: bool = False) -> dict | None:
    engine = store.engine
    repo = engine.repository
    rows = repo.latest_results(file_id)
    row = _jsonable(rows[0]) if rows else None
    if row is None and not en_disco:
        return None

    identidad = None
    ejecucion = None
    extraccion = None
    evaluacion = None
    revision = None
    if row is not None:
        identidad = {'input_id': row.get('input_id'),
                     'batch_id': row.get('batch_id'),
                     'sha256': row.get('content_hash'),
                     'bytes': row.get('size_bytes'),
                     'recibida_at': row.get('received_at')}
        ejecucion = _ejecucion(engine, repo, row)
        extraccion = _extraccion(engine, repo, row)
        evaluacion = _evaluacion(engine, row)
        revision = _revision(engine, row)

    if row is None:
        salida = {'verdict': 'ESCALAR', 'basis': 'not_processed'}
    else:
        verdict, basis = decide_output(row)
        salida = {'verdict': verdict, 'basis': basis}

    review_status = (revision or {}).get('status') or (
        ((row or {}).get('contextual_review') or {}).get('status'))
    extraction_status = (row or {}).get('extraction_status')
    etapas = [
        _etapa('recibida', 'hecha' if row else 'pendiente',
               at=identidad['recibida_at'] if identidad else None,
               ref=str(row['input_id']) if row else None),
        _etapa('extraida',
               'hecha' if extraction_status in ('completed', 'needs_review')
               else 'error' if extraction_status in ('failed', 'unknown')
               else 'pendiente'),
        _etapa('evaluada',
               'hecha' if (row or {}).get('evaluation_record_id')
               else 'pendiente',
               ref=(row or {}).get('evaluation_record_id')),
        _etapa('revisada',
               'hecha' if review_status in ('COMPLETED', 'INCOMPLETE')
               else 'error' if review_status == 'FAILED' else 'pendiente',
               ref=(row or {}).get('review_record_id')),
        _etapa('emitida', 'hecha', ref=salida['verdict']),
        _etapa('resuelta', 'no_registrada'),
        _etapa('pagada', 'no_registrada'),
    ]
    return {
        'file_id': file_id,
        'identidad': identidad,
        'ejecucion': ejecucion,
        'extraccion': extraccion,
        'evaluacion': evaluacion,
        'revision': revision,
        'salida': salida,
        'resolucion': None,
        'pago': None,
        'etapas': etapas,
    }
