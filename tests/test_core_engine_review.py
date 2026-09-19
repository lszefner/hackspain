from __future__ import annotations

import copy

import pytest

from ingestion.contracts import canonical_bytes, digest
from rules_ingestion.contextual_contracts import ReviewInputError
from rules_ingestion.contextual_review import ProviderReply, review_evaluation
from rules_ingestion.engine import InvoiceDecisionEngine
from rules_ingestion.evaluation_examples import example_bundle
from rules_ingestion.evaluator import evaluate

REVIEWED = '2026-09-19T12:00:00Z'


class ScriptedProvider:
    provider = 'scripted'
    model = 'contract-test'

    def __init__(self):
        self.calls = 0

    async def review(self, request):
        self.calls += 1
        self.last_request = copy.deepcopy(request)
        ids = [rule['rule_id'] for rule in request['evaluation']['rule_results']]
        return ProviderReply({
            'rule_reviews': [{'rule_id': rule_id, 'assessment': 'UNCERTAIN',
                              'explanation': 'Scripted contract response', 'evidence': []} for rule_id in ids],
            'findings': [{'code': 'SCRIPTED_LIMITATION', 'kind': 'REVIEW_LIMITATION',
                          'severity': 'blocking', 'rule_ids': ids,
                          'explanation': 'Not measured model accuracy', 'evidence': []}],
            'reviewed_sources': [], 'limitations': ['Scripted response'],
        }, self.model, 'scripted-request', {'total_tokens': 0})


@pytest.mark.parametrize('case', ['clean', 'database-duplicate', 'unsupported', 'bank-note'])
@pytest.mark.asyncio
async def test_real_v2_evaluation_reaches_contextual_reviewer(case):
    bundle = example_bundle(case)
    result = evaluate(bundle).to_dict()
    original = copy.deepcopy(result)
    provider = ScriptedProvider()
    review = await review_evaluation(result, bundle.context, bundle.artifacts, provider, reviewed_at=REVIEWED)
    assert provider.calls == 1
    assert review['status'] == 'INCOMPLETE'
    assert review['evaluation_id'] == result['evaluation_id']
    assert review['context_schema_version'] == 'decision-context/2'
    assert review['payment_authorized'] is False
    assert review['original_preliminary_decision'] == result['preliminary_decision']
    assert result == original


@pytest.mark.asyncio
async def test_v2_forged_evaluation_rejected_before_provider():
    bundle = example_bundle('clean')
    result = evaluate(bundle).to_dict()
    result['preliminary_decision'] = 'NO_PAGAR'
    result['evaluation_id'] = 'ev_' + digest(canonical_bytes({key: value for key, value in result.items() if key != 'evaluation_id'}))
    provider = ScriptedProvider()
    with pytest.raises(ReviewInputError):
        await review_evaluation(result, bundle.context, bundle.artifacts, provider, reviewed_at=REVIEWED)
    assert provider.calls == 0


def test_supabase_factory_never_falls_back(monkeypatch):
    for key in ('SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'DATABASE_URL', 'SUPABASE_DB_URL'):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        InvoiceDecisionEngine.from_supabase()


def test_cli_missing_credentials_has_no_secret_output(monkeypatch, capsys):
    from rules_ingestion.engine_cli import main

    monkeypatch.delenv('REVIEW_API_KEY', raising=False)
    assert main(['review', '--record-id', 'er_' + '0' * 64, '--request-key', 'intent',
                 '--reviewed-at', REVIEWED, '--endpoint', 'https://example.invalid/review', '--model', 'test']) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert 'core_engine_failed: KeyError' in captured.err


@pytest.mark.asyncio
async def test_actual_master_workbook_fits_bounded_review(tmp_path):
    from pathlib import Path

    import yaml

    from backend import caja_paths as caja
    from backend.master_data import capture_master_snapshots
    from rules_ingestion.contextual_review import PROJECTION_LIMITATION, ReviewLimits
    from rules_ingestion.decision_context import build_context
    from rules_ingestion.execution_context import prepare_context
    from tests.decision_context.test_decision_context import (
        make_outcome,
        make_ruleset,
        make_snapshots,
    )

    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / 'rules_ingestion' / 'sources.yaml').read_bytes())
    config['workbook']['path'] = str(caja.excel().resolve())
    mapping = tmp_path / 'sources.yaml'
    mapping.write_text(yaml.safe_dump(config))
    snapshots = make_snapshots()
    snapshots.update(capture_master_snapshots(str(mapping), captured_at=REVIEWED))
    bundle = prepare_context(build_context(make_outcome(), snapshots=snapshots, ruleset=make_ruleset(),
                                          evaluation_date='2026-09-19', captured_at=REVIEWED))
    result = evaluate(bundle).to_dict()
    before = copy.deepcopy((bundle.context, bundle.artifacts, result))
    provider = ScriptedProvider()
    review = await review_evaluation(result, bundle.context, bundle.artifacts, provider, reviewed_at=REVIEWED)
    assert provider.calls == 1
    assert len(canonical_bytes(provider.last_request)) <= ReviewLimits().max_input_bytes
    assert provider.last_request['source_projections']['orders']['complete'] is False
    assert review['status'] == 'INCOMPLETE'
    assert review['attention_required'] is True
    assert review['payment_authorized'] is False
    assert PROJECTION_LIMITATION in review['limitations']
    assert (bundle.context, bundle.artifacts, result) == before


def test_projected_citations_only_use_retained_original_values():
    from types import SimpleNamespace

    from rules_ingestion.contextual_contracts import pointer_get
    from rules_ingestion.contextual_review import (
        ReviewLimits,
        _retain_pointer,
        _validate_response,
        build_review_request,
    )

    document = {'records': [None, {'iban': 'HIDDEN'}, {'iban': 'other'}, {'i/ban~': 'KEPT'}], 'padding': 'x' * 50000}
    original = copy.deepcopy(document)
    pointer = '/records/3/i~1ban~0'
    evaluation = {'rule_results': [{'rule_id': 'R', 'status': 'PASS', 'complete': True,
                                    'evidence_refs': [{'source': 'orders', 'pointer': pointer}]}]}
    view = SimpleNamespace(sources={'orders': {'sha256': digest(canonical_bytes(document))}})
    request, projected = build_review_request(evaluation, {'fields': {}}, view, {'orders': document}, [], [],
                                              ReviewLimits(max_input_bytes=20000))
    assert projected is True
    assert len(canonical_bytes(request)) <= 20000
    assert pointer_get(request['sources']['orders'], pointer) == 'KEPT'
    assert request['sources']['orders']['records'][0] is None
    assert _retain_pointer({}, document, '') == document
    assert document == original

    def payload(path, quote):
        return {'rule_reviews': [{'rule_id': 'R', 'assessment': 'SUPPORTED', 'explanation': 'Source evidence',
                                 'evidence': [{'source': 'orders', 'pointer': path, 'quote': quote}]}],
                'findings': [], 'reviewed_sources': ['orders'], 'limitations': []}

    valid = payload(pointer, 'KEPT')
    _validate_response(valid, evaluation, {'orders': document})
    _validate_response(valid, evaluation, request['sources'], source_projections=request['source_projections'])
    for path, quote in (('/records/0', 'null'), ('/records/1/iban', 'HIDDEN')):
        omitted = payload(path, quote)
        _validate_response(omitted, evaluation, {'orders': document})
        with pytest.raises(ReviewInputError):
            _validate_response(omitted, evaluation, request['sources'], source_projections=request['source_projections'])


@pytest.mark.asyncio
async def test_small_packets_keep_full_source_shape_and_tiny_budget_fails():
    from rules_ingestion.contextual_review import ReviewLimits

    bundle = example_bundle('clean')
    result = evaluate(bundle).to_dict()
    provider = ScriptedProvider()
    await review_evaluation(result, bundle.context, bundle.artifacts, provider, reviewed_at=REVIEWED)
    assert 'source_projections' not in provider.last_request
    blocked = ScriptedProvider()
    review = await review_evaluation(result, bundle.context, bundle.artifacts, blocked,
                                     reviewed_at=REVIEWED, limits=ReviewLimits(max_input_bytes=1))
    assert blocked.calls == 0
    assert review['error']['code'] == 'input_too_large'
