"""Supabase-backed rule generation, extraction, evaluation and contextual review."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from contextlib import ExitStack
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# La Caja se resuelve en backend/caja_paths.py (caja/ viva, o la instantanea
# mas reciente de caja_de_alberto/). El motor no depende del paquete alberto.
from backend import caja_paths as _caja
from backend.erp_cache import capture_cached_erp
from backend.master_data import (
    ErpClient as ErpClient,  # noqa: PLC0414 - compatibility for existing backend callers
)
from backend.master_data import (
    capture_master_snapshots,
)
from backend.results_store import PostgresResultsStore
from ingestion.artifact_cache import artifact_session
from ingestion.config import credentials, settings
from ingestion.contracts import Contracts, canonical_bytes, digest
from ingestion.events import emit
from ingestion.pipeline import Pipeline
from rules_ingestion.decision_context import (
    ContextError,
    SourceSnapshot,
    _parse_ruleset,
    build_context,
    evaluate_context,
)
from rules_ingestion.decision_storage import DecisionStore
from rules_ingestion.engine import InvoiceDecisionEngine, RuleSource

FACTURAS_DIR = Path(os.environ.get('REVISION_INPUT_DIR') or _caja.facturas())
DATA_DIR = Path(__file__).resolve().parent / "data"
RULESET_PATH = ROOT / "rules_ingestion" / "outcome" / "v3" / "balanced" / "rules.json"
SOURCES_YAML = ROOT / "rules_ingestion" / "sources.yaml"
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def review_outcome(outcome: dict, *, snapshots: dict[str, SourceSnapshot],
                   ruleset: bytes | dict, evaluation_date: str,
                   captured_at: str,
                   decision_store: DecisionStore) -> tuple:
    bundle = build_context(outcome, snapshots=snapshots, ruleset=ruleset,
                           evaluation_date=evaluation_date,
                           captured_at=captured_at)
    receipt = decision_store.save(bundle)
    evaluation = evaluate_context(bundle)
    return bundle, evaluation, receipt


def _resolve_evaluation_date(value: str | None) -> str:
    candidate = value or os.environ.get("REVISION_EVALUATION_DATE")
    if candidate == "issue-date":
        return candidate
    if not candidate or not ISO_DATE.match(candidate):
        raise ValueError(
            "an explicit evaluation date (YYYY-MM-DD) is required via "
            "evaluation_date or REVISION_EVALUATION_DATE")
    try:
        from datetime import date
        date.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid evaluation date {candidate!r}") from exc
    return candidate


def _validate_file_ids(file_ids: list[str], root: Path | None = None) -> list[str]:
    if not file_ids:
        raise ValueError("no file_ids selected for revision")
    root = (root or FACTURAS_DIR).resolve()
    validated = []
    for file_id in file_ids:
        if not isinstance(file_id, str) or not file_id \
                or Path(file_id).name != file_id \
                or "/" in file_id or "\\" in file_id \
                or Path(file_id).is_absolute() \
                or not file_id.lower().endswith(".pdf"):
            raise ValueError(f"invalid file_id {file_id!r}")
        resolved = (root / file_id).resolve()
        if root not in resolved.parents:
            raise ValueError(f"file_id escapes facturas directory: {file_id!r}")
        validated.append(file_id)
    if len(set(validated)) != len(validated):
        raise ValueError("duplicate file_ids in revision request")
    return validated


def run_status(engine, request_key):
    row = engine.repository.get_run(request_key)
    if row is None:
        raise KeyError('run not found')
    header = {'request_key': request_key, 'state': row['state'],
              'batch_id': str(row['batch_id']) if row['batch_id'] else None}
    if row['result_artifact_id']:
        ref = engine.archive.ref(str(row['result_artifact_id']))
        result = engine._json(ref)
        if any(result.get(key) != value for key, value in header.items()):
            raise ContextError('run summary identity mismatch')
        return result
    return header | {'error': row['error_code']}


def _sources_from_paths(paths):
    if not isinstance(paths, dict):
        raise TypeError('REVISION_RULE_SOURCES must be a JSON object of names to paths')
    # Match generated rule sources: exact opaque bytes, independent of filename
    # or platform MIME mappings. Artifact identity is content hash plus kind.
    return [RuleSource(name, Path(path).read_bytes(), 'application/octet-stream')
            for name, path in paths.items()]


def _freeze_snapshots(engine, snapshots):
    from rules_ingestion.decision_context import _snapshot_payload_bytes

    frozen = {}
    names, items = [], []
    for name, snapshot in snapshots.items():
        metadata = asdict(snapshot)
        metadata.pop('payload')
        raw = _snapshot_payload_bytes(snapshot)
        metadata['payload_artifact'] = None
        if raw is not None:
            names.append(name)
            items.append((raw, 'engine-run-source', 'application/octet-stream'))
        frozen[name] = metadata
    for name, ref in zip(names, engine.archive.put_many(items)):
        frozen[name]['payload_artifact'] = ref
    return frozen


def _review_enabled():
    value = os.environ.get('REVISION_REVIEW_ENABLED', 'false').strip().lower()
    if value not in ('true', 'false', '1', '0'):
        raise ValueError('REVISION_REVIEW_ENABLED must be true/false or 1/0')
    return value in ('true', '1')


async def revisar_lote(file_ids: list[str], *, request_key: str | None = None,
                       store=None, evaluation_date: str | None = None,
                       backend: str | None = None, ruleset_path: str | None = None,
                       sources_yaml: str | None = None, profile_path: str | None = None,
                       rule_sources: list[RuleSource] | None = None,
                       input_dir: str | Path | None = None,
                       engine: InvoiceDecisionEngine | None = None,
                       review_provider=None) -> dict:
    from backend.rules_cache import generation_signature, reusable_rules
    from rules_ingestion import loader
    from rules_ingestion.contextual_provider import review_provider_from_environment

    started = time.monotonic()
    timings = {}
    enabled = _review_enabled()
    evaluation_date = _resolve_evaluation_date(evaluation_date)
    date_policy = 'issue-date' if evaluation_date == 'issue-date' else 'fixed'
    backend = backend or os.environ.get('REVISION_BACKEND') or 'supabase'
    if backend != 'supabase':
        raise ValueError('the backend requires Supabase; local fallback is disabled')
    root = Path(input_dir or os.environ.get('REVISION_INPUT_DIR') or FACTURAS_DIR)
    file_ids = _validate_file_ids(file_ids, root)
    pinned_path = ruleset_path or os.environ.get('REVISION_RULESET_PATH')
    if pinned_path and not Path(pinned_path).is_file():
        raise ValueError('ruleset file not found')
    ruleset_bytes = Path(pinned_path).read_bytes() if pinned_path else None
    if ruleset_bytes is not None:
        _parse_ruleset(ruleset_bytes)
    if store is not None and not isinstance(store, PostgresResultsStore):
        raise ValueError('the backend requires a PostgresResultsStore')
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise ValueError('an explicit request_key of 1..200 characters is required')
    sources_file = Path(sources_yaml or os.environ.get('REVISION_SOURCES_PATH') or SOURCES_YAML)
    loaded = loader.load(str(sources_file))
    profile_bytes = None
    if ruleset_bytes is None:
        profile_bytes = Path(profile_path or os.environ.get('REVISION_PROFILE_PATH')
                             or ROOT / 'rules_ingestion' / 'profiles' / 'balanced.yaml').read_bytes()
        rule_sources = [RuleSource('workbook.xlsx', loaded.workbook_bytes),
                        RuleSource('sources.yaml', loaded.source_config_bytes),
                        RuleSource('profile.yaml', profile_bytes)]
    elif rule_sources is None:
        rule_sources = _sources_from_paths(json.loads(os.environ.get('REVISION_RULE_SOURCES', '{}')))
    if not rule_sources or any(not isinstance(source, RuleSource)
                               or not isinstance(source.name, str) or not source.name.strip()
                               or not isinstance(source.content, bytes) or not source.content
                               or not isinstance(source.content_type, str) or not source.content_type.strip()
                               for source in rule_sources):
        raise ValueError('exact original rule_sources are required for a pinned ruleset')
    if len({source.name for source in rule_sources}) != len(rule_sources):
        raise ValueError('rule source names must be unique')
    contracts = Contracts(str(ROOT / 'benchmark' / 'schemas'))
    config = settings(interpreter='deepseek', dpi=200, concurrency=3, ocr='helmcode-vision')
    config['schema_hashes'], config['schemas'] = contracts.hashes, contracts.schemas
    secrets = credentials(config)
    provider = (review_provider or review_provider_from_environment()) if enabled else None
    rule_signature = generation_signature(loaded, profile_bytes) if ruleset_bytes is None else None
    documents = {file_id: digest((root / file_id).read_bytes()) for file_id in file_ids}
    signature = {'files': [{'file_id': name, 'sha256': sha} for name, sha in documents.items()],
                 'evaluation_date': evaluation_date, 'extraction_config': config,
                 'ruleset_sha256': digest(ruleset_bytes) if ruleset_bytes is not None else None,
                 'rule_sources': {source.name: digest(source.content) for source in rule_sources},
                 'master_workbook': digest(loaded.workbook_bytes), 'master_mapping': digest(loaded.source_config_bytes),
                 'rule_generation_signature': rule_signature,
                 'json_storage': 'postgres',
                 'reviewer': {'provider': provider.provider, 'model': provider.model,
                              'endpoint': getattr(provider, 'endpoint', None)} if enabled else {'enabled': False}}
    request_sha = digest(canonical_bytes(signature))
    if store is not None:
        if engine is not None and engine is not store.engine:
            raise ValueError('store and engine must share one repository')
        engine = store.engine
    owned = engine is None
    engine = engine or InvoiceDecisionEngine.from_supabase()
    store = store or PostgresResultsStore(engine)
    repo = engine.repository
    previous_compact = engine.storage.compact_json
    engine.storage.compact_json = True
    acquired = False
    session = ExitStack()
    session.enter_context(artifact_session())
    snapshots_task = None
    review_tasks = []
    try:
        claim, acquired = repo.claim_run(request_key, request_sha)
        if not acquired:
            return run_status(engine, request_key)
        captured_at = claim['created_at'].isoformat()
        def capture_sources():
            began = time.monotonic()
            captured = capture_master_snapshots(str(sources_file), captured_at=captured_at, loaded=loaded)
            captured['erp'], reused = capture_cached_erp(engine, captured_at=captured_at)
            return captured, _freeze_snapshots(engine, captured), time.monotonic() - began, reused

        snapshots_task = asyncio.create_task(asyncio.to_thread(capture_sources))
        original_items = []
        for name, sha in documents.items():
            raw = (root / name).read_bytes()
            if digest(raw) != sha:
                raise ContextError('PDF changed before durable capture')
            original_items.append((raw, 'original', 'application/pdf'))
        captured_refs = await asyncio.to_thread(engine.archive.put_many, original_items + [
            (source.content, 'engine-rule-source', source.content_type) for source in rule_sources])
        originals = dict(zip(documents, captured_refs[:len(documents)]))
        source_refs = [{'name': source.name, **ref}
                       for source, ref in zip(rule_sources, captured_refs[len(documents):])]
        run_input = {'schema_version': 'core-engine-run-input/1', 'request_key': request_key,
                     'request_sha256': request_sha, 'signature': signature, 'captured_at': captured_at,
                     'evaluation_date_policy': date_policy,
                     'review_policy': {'enabled': enabled, 'disabled_output': 'evaluator',
                                       'skipped_when': 'evaluator_decisive'},
                     'originals': originals, 'rule_sources': source_refs,
                     'ruleset': engine.archive.put(ruleset_bytes, 'decision-source-ruleset', 'application/octet-stream')
                     if ruleset_bytes is not None else None,
                     'rule_generation': 'pinned' if ruleset_bytes is not None else 'pending'}
        rules_started = time.monotonic()
        if ruleset_bytes is None:
            ruleset_bytes, audit, cached_rules, reused = await asyncio.to_thread(
                reusable_rules, engine, loaded, profile_bytes, signature=rule_signature, sources=rule_sources)
            rule_sources = [*rule_sources, RuleSource('generation-audit.json', canonical_bytes(audit), 'application/json')]
            run_input['rule_generation'] = cached_rules['audit']
            run_input['ruleset'] = cached_rules['ruleset']
            run_input['rules_reused'] = reused
        timings['rules_seconds'] = time.monotonic() - rules_started
        snapshots, run_input['snapshots'], timings['snapshots_seconds'], erp_reused = await snapshots_task
        run_input['erp_reused'] = erp_reused
        frozen = engine._put_json(run_input, 'engine-run-input')
        repo.set_run_input(request_key, request_sha, frozen['artifact_id'])
        manifest = [{'relative_path': name, 'file_id': name, 'source_sha256': ref['sha256'],
                     'ordinal': i, 'error': None} for i, (name, ref) in enumerate(originals.items())]
        batch = repo.create_batch(manifest, config)
        repo.set_run_batch(request_key, request_sha, batch['id'])
        for item in batch['manifest']:
            name = item['relative_path']
            ref = originals[name]
            item['_registered_input'] = repo.register_input(
                batch['id'], name, file_name=name, content_hash=ref['sha256'],
                object_key=ref['object_key'], size_bytes=ref['byte_size'])
        pipeline = Pipeline(repo, engine.storage, contracts, config, secrets)
        extraction_started = time.monotonic()
        await pipeline.run(batch)
        timings['extraction_seconds'] = time.monotonic() - extraction_started
        emit('revision_stage', stage='extraction_done', request_key=request_key,
             latency_seconds=timings['extraction_seconds'], count=len(file_ids))
        rows_by_file = {}
        for row in repo.results(batch['id']):
            if row['file_name'] not in documents or row['interpreter'] != config['interpreter']:
                raise ContextError('unexpected extraction result')
            rows_by_file.setdefault(row['file_name'], []).append(row)
        results = []
        review_slots = asyncio.Semaphore(3)
        decisions_started = time.monotonic()
        history_started = time.monotonic()
        history_all = await asyncio.to_thread(
            store.processed_history_snapshot, captured_at=captured_at)
        timings['history_seconds'] = time.monotonic() - history_started
        emit('revision_stage', stage='history_ready', request_key=request_key,
             latency_seconds=timings['history_seconds'],
             count=len(history_all.payload.get('records') or []))
        for name in file_ids:
            result = {'file_id': name, 'evaluation_record_id': None, 'review_record_id': None,
                      'evaluation_date': None, 'error': None}
            rows = rows_by_file.get(name, [])
            try:
                if len(rows) != 1 or rows[0]['status'] not in ('completed', 'needs_review'):
                    raise ContextError('extraction_missing_or_failed')
                row = rows[0]
                file_date = evaluation_date
                if date_policy == 'issue-date':
                    outcome = engine._json(engine.archive.ref(str(row['artifact_id'])))
                    issue = (outcome.get('invoice') or {}).get('issue_date')
                    try:
                        file_date = date.fromisoformat(str(issue)).isoformat() \
                            if issue is not None else None
                    except ValueError:
                        file_date = None
                    if file_date is None:
                        file_date = captured_at[:10]
                        result['evaluation_date_fallback'] = True
                result['evaluation_date'] = file_date
                current = dict(snapshots)
                # Reuse one history snapshot for the whole run. Main keeps
                # same-filename submissions in history (no per-file exclude).
                current['processed'] = history_all
                evaluation = await asyncio.to_thread(engine.evaluate, input_id=str(row['input_id']), interpreter=config['interpreter'],
                                             ruleset=ruleset_bytes, rule_sources=rule_sources, snapshots=current,
                                             evaluation_date=file_date, captured_at=captured_at)
                result.update(evaluation_record_id=evaluation['record_id'], decision=evaluation['decision'])
                emit('revision_stage', stage='evaluation_done', request_key=request_key,
                     file_id=name, decision=evaluation['decision'])

                if not enabled:
                    result.update(review_status='DISABLED', attention_required=evaluation['decision'] == 'ESCALAR')
                    results.append(result)
                    continue

                # The reviewer is a veto on payment, never an authorisation:
                # export_outcomes.decide_output only consults it when the
                # evaluator said PAGAR, and returns the evaluator's own verdict
                # otherwise. Buying a review for a decision it cannot change
                # costs a provider call whose answer is discarded by
                # construction, so the run states the skip instead of paying.
                if evaluation['decision'] != 'PAGAR':
                    result.update(review_status='SKIPPED_EVALUATOR_DECISIVE',
                                  attention_required=evaluation['decision'] == 'ESCALAR')
                    results.append(result)
                    continue

                async def review_call(record_id=evaluation['record_id'],
                                      input_id=row['input_id']):
                    async with review_slots:
                        # Review persistence is synchronous. Isolate the whole
                        # invocation so it cannot block other provider calls.
                        return await asyncio.to_thread(lambda: asyncio.run(engine.review(
                            record_id, provider=provider,
                            request_key='run-' + digest(canonical_bytes(
                                [request_key, str(input_id)])),
                            reviewed_at=datetime.now(UTC).isoformat())))

                review_tasks.append((result, asyncio.create_task(review_call())))
            except (ContextError, KeyError, ValueError) as exc:
                result['error'] = {'code': type(exc).__name__}
            results.append(result)

        async def fill_review(result, task):
            try:
                review = await asyncio.shield(task)
                reviewed = engine._json(review['review'])
                result.update(review_record_id=review['record_id'], review_status=reviewed['status'],
                              attention_required=reviewed['attention_required'], error=reviewed['error'])
            except (ContextError, KeyError, ValueError) as exc:
                result['error'] = {'code': type(exc).__name__}

        await asyncio.gather(*(fill_review(result, task)
                               for result, task in review_tasks))
        timings['evaluation_review_seconds'] = time.monotonic() - decisions_started
        failed = sum(bool(result['error']) for result in results)
        state = 'failed' if failed == len(results) else 'partial' if failed else 'completed'
        summary = {'request_key': request_key, 'batch_id': str(batch['id']), 'state': state,
                   'revision_counts': {'stored': len(results) - failed, 'failed': failed}, 'files': results,
                   'review_enabled': enabled, 'rules_reused': run_input.get('rules_reused', True),
                   'erp_reused': erp_reused,
                   'timings': timings | {'before_summary_seconds': time.monotonic() - started}}
        ref = engine._put_json(summary, 'engine-run-result')
        repo.finish_run(request_key, request_sha, state, ref['artifact_id'])
        emit('revision_completed', request_key=request_key, state=state,
             latency_seconds=time.monotonic() - started, count=len(results))
        return summary
    except BaseException:
        if acquired:
            repo.unknown_run(request_key, request_sha)
        raise
    finally:
        # A provider invocation in a worker thread cannot be cancelled safely.
        # Finish already-started review tasks before closing their repository.
        if review_tasks:
            await asyncio.gather(*(task for _, task in review_tasks), return_exceptions=True)
        if snapshots_task is not None:
            await asyncio.gather(snapshots_task, return_exceptions=True)
        session.close()
        engine.storage.compact_json = previous_compact
        if owned:
            engine.close()


def revisar_lote_sync(file_ids: list[str], **kwargs) -> dict:
    return asyncio.run(revisar_lote(file_ids, **kwargs))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m backend.run_revision')
    parser.add_argument('file_ids', nargs='*')
    parser.add_argument('--request-key')
    parser.add_argument('--status-key')
    parser.add_argument('--evaluation-date')
    parser.add_argument('--input-dir')
    parser.add_argument('--ruleset')
    parser.add_argument('--sources')
    parser.add_argument('--profile')
    parser.add_argument('--rule-source', action='append', default=[])
    args = parser.parse_args(argv)
    if args.status_key:
        if args.file_ids or args.request_key:
            parser.error('--status-key cannot start a new run')
        engine = InvoiceDecisionEngine.from_supabase()
        try:
            result = run_status(engine, args.status_key)
        finally:
            engine.close()
    else:
        from rules_ingestion.engine_cli import _rule_source

        result = revisar_lote_sync(args.file_ids, request_key=args.request_key, evaluation_date=args.evaluation_date,
                                   input_dir=args.input_dir, ruleset_path=args.ruleset, sources_yaml=args.sources,
                                   profile_path=args.profile,
                                   rule_sources=[_rule_source(value) for value in args.rule_source] or None)
    print(canonical_bytes(result).decode())
    return 0 if result['state'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
