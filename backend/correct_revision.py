"""Append corrected evaluations to existing submissions, without re-extraction.

This explicit operator path requires disabled review on the source runs. It
freezes a new intent and source snapshot, keeps every older evaluation, and
excludes the same file's earlier evaluations from submission-duplicate checks.
Other files remain in history, including real duplicate submissions.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from datetime import UTC, date, datetime
from pathlib import Path

from backend.audit_reader import AuditReader
from backend.master_data import (
    ErpClient,
    capture_erp_snapshot,
    capture_master_snapshots,
)
from backend.results_store import PostgresResultsStore
from backend.run_revision import _freeze_snapshots
from ingestion.artifact_cache import artifact_session
from ingestion.contracts import canonical_bytes, digest
from ingestion.events import emit
from rules_ingestion import evaluator, execution_context, loader
from rules_ingestion.decision_context import ContextError, _parse_ruleset, build_context
from rules_ingestion.engine import InvoiceDecisionEngine


def correct_existing(engine, file_ids, *, request_key, ruleset, rule_sources,
                     sources_yaml, evaluation_date, input_dir, preview=False):
    """Correct the latest stored inputs; preview performs no database writes."""
    if not isinstance(request_key, str) or not 1 <= len(request_key) <= 200:
        raise ValueError('an explicit request key of 1..200 characters is required')
    if not file_ids or len(set(file_ids)) != len(file_ids):
        raise ValueError('distinct file IDs are required')
    if evaluation_date != 'issue-date':
        date.fromisoformat(evaluation_date)
    _parse_ruleset(ruleset)
    loaded = loader.load(str(sources_yaml))
    store = PostgresResultsStore(engine)
    rows = {r['file_id']: r for r in engine.repository.latest_results()}
    selected = []
    for name in file_ids:
        if Path(name).name != name:
            raise ValueError('file IDs must be basenames')
        row = rows.get(name)
        if row is None or row['extraction_status'] not in ('completed', 'needs_review'):
            raise ContextError(f'completed extraction required: {name}')
        if row['run_state'] == 'running':
            raise ContextError('stop the original run before correcting its inputs')
        if (row.get('review_policy') or {}).get('enabled') is not False:
            raise ContextError('this correction path requires durably disabled review')
        if digest((Path(input_dir) / name).read_bytes()) != row['content_hash']:
            raise ContextError('local PDF does not match the stored submission')
        selected.append(row)
    signature = {
        'schema_version': 'revision-correction-intent/1',
        'inputs': [{'file_id': r['file_id'], 'input_id': str(r['input_id']),
                    'sha256': r['content_hash'], 'outcome_artifact_id': str(r['outcome_artifact_id'])}
                   for r in selected],
        'ruleset_sha256': digest(ruleset),
        'rule_sources': {s.name: digest(s.content) for s in rule_sources},
        'sources_yaml_sha256': digest(Path(sources_yaml).read_bytes()),
        'master_workbook_sha256': digest(loaded.workbook_bytes),
        'evaluation_date': evaluation_date, 'review_enabled': False,
        'same_submission_correction': True,
        'implementation_sha256': evaluator.implementation_identity(),
    }
    request_sha = digest(canonical_bytes(signature))
    if not preview:
        claim, acquired = engine.repository.claim_run(request_key, request_sha)
        if not acquired:
            return AuditReader(engine.repository).run_status(request_key)
        captured_at = claim['created_at'].isoformat()
    else:
        captured_at = datetime.now(UTC).isoformat()
    started = time.monotonic()
    previous_compact = engine.storage.compact_json
    engine.storage.compact_json = True
    try:
        with artifact_session():
            snapshots = capture_master_snapshots(str(sources_yaml), captured_at=captured_at, loaded=loaded)
            snapshots['erp'] = capture_erp_snapshot(ErpClient(), captured_at=captured_at)
            if (snapshots['erp'].availability != 'available'
                    or snapshots['erp'].payload.get('complete') is not True):
                raise ContextError('ERP snapshot is incomplete; no corrections were evaluated')
            emit('correction_erp_ready', request_key=request_key,
                 count=len(snapshots['erp'].payload['records']))
            # Freeze history once: a correction does not create new submissions.
            emit('correction_history_started', request_key=request_key)
            history = store.processed_history_snapshot(captured_at=captured_at)
            emit('correction_history_ready', request_key=request_key, count=len(history.payload['records']))
            from dataclasses import replace

            if not preview:
                run_input = {'schema_version': 'revision-correction-input/1',
                             'request_key': request_key,
                             'signature': signature, 'captured_at': captured_at,
                             'evaluation_date_policy': evaluation_date,
                             'review_policy': {'enabled': False, 'disabled_output': 'evaluator'},
                             'supersedes': [{'file_id': r['file_id'], 'request_key': r['request_key'],
                                            'evaluation_record_id': r['evaluation_record_id']}
                                           for r in selected],
                             'snapshots': _freeze_snapshots(engine, snapshots | {'processed': history})}
                run_input['ruleset'] = engine.archive.put(ruleset, 'decision-source-ruleset', 'application/octet-stream')
                refs = engine.archive.put_many((s.content, 'engine-rule-source', s.content_type)
                                               for s in rule_sources)
                run_input['rule_sources'] = [{'name': s.name, **ref} for s, ref in zip(rule_sources, refs)]
                run_input['rule_generation'] = 'pinned'
                ref = engine._put_json(run_input, 'engine-run-input')
                engine.repository.set_run_input(request_key, request_sha, ref['artifact_id'])
            reader = AuditReader(engine.repository)
            reader.prefetch([r['outcome_artifact_id'] for r in selected])
            results = []
            def evaluate_row(row):
                name = row['file_id']
                outcome = reader.json(str(row['outcome_artifact_id']))
                file_date = evaluation_date
                if file_date == 'issue-date':
                    try:
                        file_date = date.fromisoformat(outcome['invoice']['issue_date']).isoformat()
                    except (KeyError, TypeError, ValueError):
                        file_date = captured_at[:10]
                current = snapshots | {'processed': replace(history, payload={
                    **history.payload,
                    'records': [r for r in history.payload['records'] if r['file_id'] != name],
                    'correction_request_key': request_key,
                    'excluded_same_submission_file_id': name,
                })}
                if preview:
                    bundle = execution_context.prepare_context(build_context(
                        outcome, ruleset=ruleset, snapshots=current,
                        evaluation_date=file_date, captured_at=captured_at))
                    evaluation = evaluator.evaluate(bundle).to_dict()
                    result = {'file_id': name, 'decision': evaluation['preliminary_decision'],
                              'reason_codes': sorted({r['code'] for r in evaluation['decision_reasons']})}
                else:
                    packet = engine.evaluate(
                        input_id=str(row['input_id']), interpreter='deepseek', ruleset=ruleset,
                        rule_sources=rule_sources, snapshots=current, evaluation_date=file_date,
                        captured_at=captured_at, correction_request_key=request_key)
                    result = {'file_id': name, 'decision': packet['decision'],
                              'evaluation_record_id': packet['record_id'], 'review_status': 'DISABLED',
                              'error': None}
                return result

            def completed_rows():
                if preview:
                    for row in selected:
                        yield row, evaluate_row(row)
                    return
                # At most three outstanding independent evaluations. Share the
                # bounded, thread-safe artifact session, not thread-local defaults.
                with ThreadPoolExecutor(max_workers=3) as pool:
                    remaining = iter(selected)
                    pending = {}

                    def submit_next():
                        row = next(remaining, None)
                        if row is not None:
                            pending[pool.submit(copy_context().run, evaluate_row, row)] = row

                    for _ in range(3):
                        submit_next()
                    while pending:
                        done, _ = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            row = pending.pop(future)
                            yield row, future.result()
                            submit_next()

            for index, (row, result) in enumerate(completed_rows(), 1):
                results.append(result)
                emit('correction_invoice_evaluated', request_key=request_key,
                     input_id=row['input_id'], count=index, state=result['decision'],
                     latency_seconds=round(time.monotonic() - started, 2))
            by_file = {r['file_id']: r for r in results}
            results = [by_file[name] for name in file_ids]
            summary = {'request_key': request_key, 'batch_id': None, 'state': 'completed',
                       'review_enabled': False, 'files': results,
                       'revision_counts': {'stored': 0 if preview else len(results), 'failed': 0},
                       'decisions': dict(Counter(r['decision'] for r in results)), 'preview': preview}
            if not preview:
                ref = engine._put_json(summary, 'engine-run-result')
                engine.repository.finish_run(request_key, request_sha, 'completed', ref['artifact_id'])
            return summary
    except BaseException:
        if not preview:
            engine.repository.unknown_run(request_key, request_sha)
        raise
    finally:
        engine.storage.compact_json = previous_compact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-key', required=True)
    parser.add_argument('--input-dir', required=True)
    parser.add_argument('--ruleset', required=True)
    parser.add_argument('--rule-source', action='append', required=True)
    parser.add_argument('--sources', default='rules_ingestion/sources.yaml')
    parser.add_argument('--evaluation-date', required=True)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    from rules_ingestion.engine_cli import _rule_source

    engine = InvoiceDecisionEngine.from_supabase()
    try:
        result = correct_existing(
            engine, sorted(p.name for p in Path(args.input_dir).glob('*.pdf')),
            request_key=args.request_key, ruleset=Path(args.ruleset).read_bytes(),
            rule_sources=[_rule_source(v) for v in args.rule_source], sources_yaml=args.sources,
            evaluation_date=args.evaluation_date, input_dir=args.input_dir, preview=args.preview)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items() if k != 'files'}), flush=True)
        return 0 if result['state'] == 'completed' else 1
    finally:
        engine.close()


if __name__ == '__main__':
    raise SystemExit(main())
