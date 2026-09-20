from urllib.parse import parse_qs

import pytest
from test_backend_engine import runtime  # noqa: F401

from backend import run_revision as rr
from backend.export_outcomes import decide_output
from backend.results_store import PostgresResultsStore
from backend.ui_queries import DeskQueries, params


def test_query_bounds():
    for query in ('limit=101', 'page=0', 'action=paid', 'lifecycle=approved'):
        with pytest.raises(ValueError):
            params(parse_qs(query))
    assert params({'action': ['PAY']})['action'] == 'PAGAR'


@pytest.mark.asyncio
async def test_read_models_use_persisted_data_without_storage(runtime):  # noqa: F811
    engine, kwargs, calls, _, _ = runtime
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    before = dict(calls)
    desk = DeskQueries(PostgresResultsStore(engine))
    def forbidden(*args, **kwargs):
        raise AssertionError('UI query attempted an artifact download')
    engine.storage.get = forbidden
    result = desk.invoices({})
    assert result['grand'] == result['matched'] == 1
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['file_id'] == 'invoice.pdf'
    assert row['lifecycle'] == 'processed'
    detail = desk.detail('invoice.pdf')
    assert row['verdict'] == detail['salida']['verdict']
    assert detail['checks'] and detail['invoice']
    assert detail['lifecycle'][-1]['state'] == 'not_recorded'
    assert detail['salida']['verdict'] == decide_output({
        'evaluation_record_id': detail['version'], 'decision': detail['evaluation']['preliminary_decision'],
        'contextual_review': detail['review'],
    })[0]
    assert desk.invoices({'q':['not-found']})['matched'] == 0
    assert desk.invoices({'page':['2'], 'limit':['1']})['rows'] == []
    suppliers = desk.suppliers({})
    assert sum(lane['count'] for lane in suppliers['lanes']) == 1
    assert desk.summary()['total'] == 1
    assert desk.rules()['checked'] == 1
    assert desk.detail('missing.pdf') is None
    assert calls == before


def test_sql_recommendation_matches_canonical_policy(db):
    import json

    from backend.ui_queries import _VERDICT_SQL
    from rules_ingestion.engine_storage import EngineRepository

    repo = EngineRepository(db.dsn)
    try:
        reviews = [None, {'status':'FAILED'}, {'status':'COMPLETED'},
                   {'status':'INCOMPLETE','attention_required':True},
                   {'status':'COMPLETED','rule_reviews':[{'assessment':'CHALLENGED'}]},
                   {'status':'COMPLETED','findings':[{'severity':'blocking','kind':'REVIEW_LIMITATION'}]},
                   {'status':'COMPLETED','findings':[{'severity':'blocking','kind':'CONTRADICTION'}]}]
        for decision in ['PAGAR','ESCALAR','NO_PAGAR',None]:
            for review in reviews:
                for policy in [{}, {'enabled':False,'disabled_output':'evaluator'}]:
                    row = {'evaluation_record_id':'er_test','decision':decision,
                           'extraction_status':'completed','review':review or {},'review_policy':policy}
                    result = repo._query('SELECT '+_VERDICT_SQL+''' AS verdict FROM jsonb_to_record(%s::jsonb)
AS x(evaluation_record_id text,decision text,extraction_status text,review jsonb,review_policy jsonb)''', (json.dumps(row),))
                    assert result['verdict'] == decide_output(row | {'contextual_review':review})[0]
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_latest_input_replaces_old_result_and_failures_remain_visible(runtime):  # noqa: F811
    engine, kwargs, _, _, _ = runtime
    await rr.revisar_lote(['invoice.pdf'], **kwargs)
    desk = DeskQueries(PostgresResultsStore(engine))
    assert desk.invoices({'lifecycle':['processed']})['matched'] == 1
    repo = engine.repository
    new = repo._query('''INSERT INTO ingestion.inputs(batch_id,relative_path,file_name,created_at)
 SELECT batch_id,'new-attempt.pdf',file_name,created_at+interval '1 second' FROM ingestion.inputs
 WHERE file_name='invoice.pdf' RETURNING id''')
    repo._query('''INSERT INTO ingestion.input_results(input_id,interpreter,status,error)
 VALUES(%s,'deepseek','failed','{"code":"test_failure"}') RETURNING input_id''',(new['id'],))
    assert desk.invoices({'lifecycle':['processed']})['matched'] == 0
    result = desk.invoices({'lifecycle':['error']})
    assert result['grand'] == result['matched'] == 1
    assert result['rows'][0]['verdict'] == 'ESCALAR'
    assert result['rows'][0]['total'] is None
    assert result['rows'][0]['currency'] is None
    assert desk.suppliers({})['lanes'][0]['review_total'] is None
