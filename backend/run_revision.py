"""Supabase-backed rule generation, extraction, evaluation and contextual review."""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import re
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# La Caja se resuelve en backend/caja_paths.py (caja/ viva, o la instantanea
# mas reciente de caja_de_alberto/). El motor no depende del paquete alberto.
from backend import caja_paths as _caja
from backend.master_data import (
    ErpClient,
    capture_erp_snapshot,
    capture_master_snapshots,
)
from backend.results_store import PostgresResultsStore
from ingestion.config import credentials, settings
from ingestion.contracts import Contracts, canonical_bytes, digest
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
    return [RuleSource(name, Path(path).read_bytes(), mimetypes.guess_type(path)[0] or 'application/octet-stream')
            for name, path in paths.items()]


def _freeze_snapshots(engine, snapshots):
    from rules_ingestion.decision_context import _snapshot_payload_bytes

    frozen = {}
    for name, snapshot in snapshots.items():
        metadata = asdict(snapshot)
        metadata.pop('payload')
        raw = _snapshot_payload_bytes(snapshot)
        metadata['payload_artifact'] = (engine.archive.put(raw, 'engine-run-source', 'application/octet-stream')
                                        if raw is not None else None)
        frozen[name] = metadata
    return frozen


async def revisar_lote(file_ids: list[str], *, request_key: str | None = None,
                       store=None, evaluation_date: str | None = None,
                       backend: str | None = None, ruleset_path: str | None = None,
                       sources_yaml: str | None = None, profile_path: str | None = None,
                       rule_sources: list[RuleSource] | None = None,
                       input_dir: str | Path | None = None,
                       engine: InvoiceDecisionEngine | None = None,
                       review_provider=None) -> dict:
    from rules_ingestion import loader
    from rules_ingestion.build_rules import build_frozen_rules
    from rules_ingestion.contextual_provider import review_provider_from_environment

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
        if any(not os.environ.get(key) for key in ('JEV_API_KEY', 'HELMCODE_API_KEY')):
            raise ValueError('AI rule generation requires JEV_API_KEY and HELMCODE_API_KEY')
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
    provider = review_provider or review_provider_from_environment()
    documents = {file_id: digest((root / file_id).read_bytes()) for file_id in file_ids}
    signature = {'files': [{'file_id': name, 'sha256': sha} for name, sha in documents.items()],
                 'evaluation_date': evaluation_date, 'extraction_config': config,
                 'ruleset_sha256': digest(ruleset_bytes) if ruleset_bytes is not None else None,
                 'rule_sources': {source.name: digest(source.content) for source in rule_sources},
                 'master_workbook': digest(loaded.workbook_bytes), 'master_mapping': digest(loaded.source_config_bytes),
                 'reviewer': {'provider': provider.provider, 'model': provider.model,
                              'endpoint': getattr(provider, 'endpoint', None)}}
    request_sha = digest(canonical_bytes(signature))
    if store is not None:
        if engine is not None and engine is not store.engine:
            raise ValueError('store and engine must share one repository')
        engine = store.engine
    owned = engine is None
    engine = engine or InvoiceDecisionEngine.from_supabase()
    store = store or PostgresResultsStore(engine)
    repo = engine.repository
    acquired = False
    try:
        claim, acquired = repo.claim_run(request_key, request_sha)
        if not acquired:
            return run_status(engine, request_key)
        captured_at = claim['created_at'].isoformat()
        originals = {}
        for name, sha in documents.items():
            raw = (root / name).read_bytes()
            if digest(raw) != sha:
                raise ContextError('PDF changed before durable capture')
            originals[name] = engine.archive.put(raw, 'original', 'application/pdf')
        source_refs = [{'name': source.name, **engine.archive.put(source.content, 'engine-rule-source', source.content_type)}
                       for source in rule_sources]
        run_input = {'schema_version': 'core-engine-run-input/1', 'request_key': request_key,
                     'request_sha256': request_sha, 'signature': signature, 'captured_at': captured_at,
                     'evaluation_date_policy': date_policy,
                     'originals': originals, 'rule_sources': source_refs,
                     'ruleset': engine.archive.put(ruleset_bytes, 'decision-source-ruleset', 'application/octet-stream')
                     if ruleset_bytes is not None else None,
                     'rule_generation': 'pinned' if ruleset_bytes is not None else 'pending'}
        initial = engine._put_json(run_input, 'engine-run-input')
        repo.set_run_input(request_key, request_sha, initial['artifact_id'])
        if ruleset_bytes is None:
            ruleset_bytes, audit = build_frozen_rules(loaded, profile_bytes, generated_at=captured_at)
            _parse_ruleset(ruleset_bytes)
            rule_sources = [*rule_sources, RuleSource('generation-audit.json', canonical_bytes(audit), 'application/json')]
            run_input['rule_generation'] = engine._put_json(audit, 'engine-rule-generation')
            run_input['ruleset'] = engine.archive.put(ruleset_bytes, 'decision-source-ruleset', 'application/octet-stream')
        snapshots = capture_master_snapshots(str(sources_file), captured_at=captured_at, loaded=loaded)
        snapshots['erp'] = capture_erp_snapshot(ErpClient(), captured_at=captured_at)
        run_input['snapshots'] = _freeze_snapshots(engine, snapshots)
        frozen = engine._put_json(run_input, 'engine-run-input', [initial['artifact_id']])
        repo.set_run_input(request_key, request_sha, frozen['artifact_id'])
        manifest = [{'relative_path': name, 'file_id': name, 'source_sha256': ref['sha256'],
                     'ordinal': i, 'error': None} for i, (name, ref) in enumerate(originals.items())]
        batch = repo.create_batch(manifest, config)
        repo.set_run_batch(request_key, request_sha, batch['id'])
        for name, ref in originals.items():
            repo.register_input(batch['id'], name, file_name=name, content_hash=ref['sha256'],
                                object_key=ref['object_key'], size_bytes=ref['byte_size'])
        pipeline = Pipeline(repo, engine.storage, contracts, config, secrets)
        await pipeline.run(batch)
        rows_by_file = {}
        for row in repo.results(batch['id']):
            if row['file_name'] not in documents or row['interpreter'] != config['interpreter']:
                raise ContextError('unexpected extraction result')
            rows_by_file.setdefault(row['file_name'], []).append(row)
        results = []
        review_tasks = []
        review_slots = asyncio.Semaphore(3)
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
                current['processed'] = store.processed_history_snapshot(captured_at=captured_at, exclude_file_id=name)
                evaluation = engine.evaluate(input_id=str(row['input_id']), interpreter=config['interpreter'],
                                             ruleset=ruleset_bytes, rule_sources=rule_sources, snapshots=current,
                                             evaluation_date=file_date, captured_at=captured_at)
                result.update(evaluation_record_id=evaluation['record_id'], decision=evaluation['decision'])

                async def review_call(record_id=evaluation['record_id'],
                                      input_id=row['input_id']):
                    async with review_slots:
                        return await engine.review(
                            record_id, provider=provider,
                            request_key='run-' + digest(canonical_bytes(
                                [request_key, str(input_id)])),
                            reviewed_at=datetime.now(UTC).isoformat())

                review_tasks.append((result, asyncio.create_task(review_call())))
            except (ContextError, KeyError, ValueError) as exc:
                result['error'] = {'code': type(exc).__name__}
            results.append(result)

        async def fill_review(result, task):
            try:
                review = await task
                reviewed = engine._json(review['review'])
                result.update(review_record_id=review['record_id'], review_status=reviewed['status'],
                              attention_required=reviewed['attention_required'], error=reviewed['error'])
            except (ContextError, KeyError, ValueError) as exc:
                result['error'] = {'code': type(exc).__name__}

        await asyncio.gather(*(fill_review(result, task)
                               for result, task in review_tasks))
        failed = sum(bool(result['error']) for result in results)
        state = 'failed' if failed == len(results) else 'partial' if failed else 'completed'
        summary = {'request_key': request_key, 'batch_id': str(batch['id']), 'state': state,
                   'revision_counts': {'stored': len(results) - failed, 'failed': failed}, 'files': results}
        ref = engine._put_json(summary, 'engine-run-result')
        repo.finish_run(request_key, request_sha, state, ref['artifact_id'])
        return summary
    except BaseException:
        if acquired:
            repo.unknown_run(request_key, request_sha)
        raise
    finally:
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
