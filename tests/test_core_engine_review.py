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
