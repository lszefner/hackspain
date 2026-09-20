"""Read models for the desk and its agent; no provider calls or Storage reads.

Use canonical artifact payloads already persisted in Postgres. No backfill or
new mutable copy of an evaluation is needed. Lists never hydrate audit packets.
"""
from __future__ import annotations

from collections import Counter

from backend.export_outcomes import REVIEW_NOT_PURCHASED, decide_output
from rules_ingestion.decision_storage import _jsonable

_VERDICT_SQL = """CASE WHEN evaluation_record_id IS NULL OR extraction_status IN ('failed','unknown') THEN 'ESCALAR'
 WHEN decision IS DISTINCT FROM 'PAGAR' THEN CASE WHEN decision = 'NO_PAGAR' THEN 'NO_PAGAR' ELSE 'ESCALAR' END
 WHEN review_policy->>'enabled' = 'false' AND review_policy->>'disabled_output' = 'evaluator' THEN 'PAGAR'
 WHEN review->>'status' IN ('COMPLETED','INCOMPLETE')
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(review->'rule_reviews','[]')) item WHERE item->>'assessment' = 'CHALLENGED')
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(review->'findings','[]')) item WHERE item->>'severity' = 'blocking' AND item->>'kind' IS DISTINCT FROM 'REVIEW_LIMITATION')
 THEN 'PAGAR' ELSE 'ESCALAR' END"""

# Select the latest persisted input per file BEFORE filtering by processing
# status, so an older successful run cannot hide a newer failed attempt.
_BASE = """
WITH latest AS (
 SELECT DISTINCT ON (i.file_name) i.* FROM ingestion.inputs i
 ORDER BY i.file_name, i.created_at DESC, i.id DESC
), source AS (
 SELECT i.file_name AS file_id, i.id::text AS input_id, i.batch_id::text,
 i.created_at AS received_at, i.content_hash, i.object_key,
 r.status AS extraction_status, r.error AS extraction_error,
 e.record_id AS evaluation_record_id, e.decision, e.created_at AS evaluated_at,
 v.record_id AS review_record_id, v.created_at AS reviewed_at,
 run.request_key, run.state AS run_state, run.error_code AS run_error,
 frozen.payload->'review_policy' AS review_policy,
 coalesce(review.payload, '{}'::jsonb) AS review,
 context.payload->'fields' AS fields,
 evaluation.payload AS evaluation,
 outcome.payload->'invoice' AS invoice,
 outcome.payload->'extraction'->>'route' AS extraction_route,
 outcome.payload->>'cost_usd' AS cost_usd,
 packet.payload->'ruleset' AS ruleset_ref
 FROM latest i JOIN ingestion.batches b ON b.id = i.batch_id
 LEFT JOIN ingestion.input_results r ON r.input_id = i.id AND r.interpreter = b.config->>'interpreter'
 LEFT JOIN LATERAL (
   SELECT er.* FROM ingestion.engine_records er WHERE er.input_id = i.id AND er.kind = 'evaluation'
   ORDER BY er.created_at DESC, er.record_id DESC LIMIT 1
 ) e ON true
 LEFT JOIN LATERAL (
   SELECT er.* FROM ingestion.engine_records er WHERE er.parent_record_id = e.record_id AND er.kind = 'review'
   ORDER BY er.created_at DESC, er.record_id DESC LIMIT 1
 ) v ON true
 LEFT JOIN ingestion.artifacts packet ON packet.id = e.artifact_id
 LEFT JOIN ingestion.artifacts context ON context.id = (packet.payload->'context'->>'artifact_id')::uuid
 LEFT JOIN ingestion.artifacts evaluation ON evaluation.id = (packet.payload->'evaluation'->>'artifact_id')::uuid
 LEFT JOIN ingestion.artifacts outcome ON outcome.id = r.artifact_id
 LEFT JOIN ingestion.artifacts review_packet ON review_packet.id = v.artifact_id
 LEFT JOIN ingestion.artifacts review ON review.id = (review_packet.payload->'review'->>'artifact_id')::uuid
 LEFT JOIN LATERAL (
   SELECT er.* FROM ingestion.engine_runs er WHERE er.batch_id = i.batch_id
   ORDER BY er.created_at DESC, er.request_key DESC LIMIT 1
 ) run ON true
 LEFT JOIN ingestion.artifacts frozen ON frozen.id = run.input_artifact_id
), projected AS (
 SELECT source.*,
 coalesce(nullif(invoice->'supplier'->>'name',''), fields->'supplier.id'->>'value', 'Supplier not recorded') AS vendor,
 coalesce(nullif(fields->'supplier.id'->>'value',''), nullif(invoice->'supplier'->>'tax_id',''), 'unknown:' || input_id) AS vendor_id,
 coalesce(invoice->>'invoice_number', fields->'invoice.number'->>'value') AS number,
 coalesce(invoice->>'issue_date', fields->'invoice.issue_date'->>'value') AS date,
 coalesce(invoice->>'currency', fields->'invoice.currency'->>'value') AS currency,
 CASE WHEN coalesce(invoice->'totals'->>'total', fields->'invoice.total'->>'value') ~ '^-?[0-9]+([.][0-9]+)?$'
 THEN coalesce(invoice->'totals'->>'total', fields->'invoice.total'->>'value')::numeric END AS total,
 coalesce(review->>'status', CASE WHEN review_policy->>'enabled' = 'false' AND evaluation_record_id IS NOT NULL THEN 'DISABLED' END) AS review_status,
 CASE WHEN extraction_status IN ('failed','unknown') OR run_state IN ('failed','unknown') THEN 'error'
 WHEN review->>'status' = 'FAILED' THEN 'error'
 WHEN evaluation_record_id IS NOT NULL THEN 'processed'
 WHEN extraction_status IN ('completed','needs_review') THEN 'extracted'
 ELSE 'processing' END AS lifecycle,
 __OUTPUT_POLICY__ AS verdict
 FROM source
), compact AS (
 SELECT file_id, input_id, batch_id, request_key, received_at, evaluated_at,
 evaluation_record_id, review_record_id, vendor, vendor_id, number, date, total,
 currency, extraction_status, extraction_route, lifecycle, review_status, verdict,
 CASE WHEN review_status IN ('DISABLED','SKIPPED_EVALUATOR_DECISIVE') THEN verdict='ESCALAR' ELSE coalesce((review->>'attention_required')::boolean,true) END AS attention_required,
 coalesce(extraction_error, to_jsonb(run_error)) AS error,
 coalesce((SELECT jsonb_object_agg(status,n) FROM (
 SELECT item->>'status' AS status,count(*) AS n FROM jsonb_array_elements(coalesce(evaluation->'rule_results','[]')) item GROUP BY 1
 ) counts), '{}'::jsonb) AS rule_counts,
 (SELECT item->>'explanation' FROM jsonb_array_elements(coalesce(evaluation->'rule_results','[]')) item
 WHERE item->>'status' NOT IN ('PASS','NOT_APPLICABLE') LIMIT 1) AS reason
 FROM projected
), filtered AS (
 SELECT * FROM compact WHERE
 (%(q)s = '' OR strpos(lower(concat_ws(' ',file_id,vendor,number,vendor_id)),lower(%(q)s)) > 0)
 AND (%(action)s = '' OR verdict = %(action)s)
 AND (%(lifecycle)s = '' OR lifecycle = %(lifecycle)s)
 AND (%(vendor)s = '' OR vendor_id = %(vendor)s)
 AND (%(currency)s = '' OR coalesce(currency,'UNKNOWN') = %(currency)s)
)
"""
_BASE = _BASE.replace("__OUTPUT_POLICY__", _VERDICT_SQL)


def params(query):
    value = lambda key, default='': (query.get(key) or [default])[0]
    page = int(value('page', '1'))
    limit = int(value('limit', '50'))
    if page < 1 or not 1 <= limit <= 100:
        raise ValueError('invalid_pagination')
    action = {'PAY': 'PAGAR', 'ESCALATE': 'ESCALAR', 'DO NOT PAY': 'NO_PAGAR'}.get(value('action'), value('action'))
    if action not in ('', 'PAGAR', 'ESCALAR', 'NO_PAGAR'):
        raise ValueError('invalid_action')
    lifecycle = value('lifecycle')
    if lifecycle not in ('', 'processed', 'extracted', 'processing', 'error'):
        raise ValueError('invalid_lifecycle')
    return {'q': value('q')[:200], 'action': action, 'lifecycle': lifecycle,
            'vendor': value('vendor')[:200], 'currency': value('currency')[:20],
            'limit': limit, 'offset': (page - 1)*limit, 'page': page}


class DeskQueries:
    def __init__(self, store):
        self.store = store
        self.repo = store.engine.repository

    def invoices(self, query):
        p = params(query)
        result = self.repo._query(_BASE + """
 SELECT (SELECT count(*) FROM compact) AS grand,
 (SELECT count(*) FROM filtered) AS matched,
 coalesce((SELECT jsonb_agg(to_jsonb(rows)) FROM (
 SELECT * FROM filtered ORDER BY received_at DESC,input_id DESC LIMIT %(limit)s OFFSET %(offset)s
 ) rows), '[]'::jsonb) AS rows
""", p)
        return _jsonable(result) | {'page': p['page'], 'limit': p['limit']}

    def suppliers(self, query):
        p = params(query)
        result = self.repo._query(_BASE + """, grouped AS (
 SELECT vendor_id AS id, min(vendor) AS name, coalesce(currency,'UNKNOWN') AS currency,
 count(*) AS count,
 count(*) FILTER (WHERE verdict='PAGAR') AS pay_n,
 count(*) FILTER (WHERE verdict='ESCALAR') AS review_n,
 count(*) FILTER (WHERE verdict='NO_PAGAR') AS nopay_n,
 CASE WHEN currency IS NOT NULL THEN coalesce(sum(total) FILTER (WHERE verdict='PAGAR'),0) END AS pay_total,
 CASE WHEN currency IS NOT NULL THEN coalesce(sum(total) FILTER (WHERE verdict='ESCALAR'),0) END AS review_total,
 CASE WHEN currency IS NOT NULL THEN coalesce(sum(total) FILTER (WHERE verdict='NO_PAGAR'),0) END AS nopay_total,
 count(*) FILTER (WHERE total IS NULL) AS missing_amounts
 FROM filtered GROUP BY vendor_id,currency
 ) SELECT (SELECT count(*) FROM compact) AS grand,
 (SELECT count(*) FROM filtered) AS matched,
 (SELECT count(*) FROM grouped) AS supplier_count,
 (SELECT jsonb_build_object('PAY',count(*) FILTER (WHERE verdict='PAGAR'),
 'ESCALATE',count(*) FILTER (WHERE verdict='ESCALAR'),
 'DO NOT PAY',count(*) FILTER (WHERE verdict='NO_PAGAR')) FROM filtered) AS counts,
 coalesce((SELECT jsonb_agg(to_jsonb(rows)) FROM (
 SELECT * FROM grouped ORDER BY review_n DESC,count DESC,id,currency LIMIT %(limit)s OFFSET %(offset)s
 ) rows),'[]'::jsonb) AS lanes
""", p)
        return _jsonable(result) | {'page': p['page'], 'limit': p['limit']}

    def summary(self):
        return _jsonable(self.repo._query(_BASE + """
 SELECT (SELECT count(*) FROM compact) AS total,
 (SELECT count(DISTINCT vendor_id) FROM compact) AS suppliers,
 coalesce((SELECT jsonb_object_agg(lifecycle,n) FROM (SELECT lifecycle,count(*) n FROM compact GROUP BY lifecycle) s),'{}') AS lifecycle,
 coalesce((SELECT jsonb_agg(to_jsonb(s)) FROM (
 SELECT coalesce(currency,'UNKNOWN') AS currency, verdict,count(*) AS n,
 CASE WHEN currency IS NOT NULL THEN sum(total) END AS total,count(*) FILTER (WHERE total IS NULL) AS missing_amounts
 FROM compact GROUP BY currency,verdict ORDER BY currency,verdict) s),'[]') AS totals
""", params({})))

    def rules(self):
        return _jsonable(self.repo._query(_BASE + """
 SELECT coalesce((SELECT jsonb_agg(to_jsonb(s)) FROM (
 SELECT evaluation->'ruleset' AS ruleset,item->>'rule_id' AS rule_id,
 item->>'status' AS status,count(*) AS n
 FROM projected CROSS JOIN LATERAL jsonb_array_elements(coalesce(evaluation->'rule_results','[]')) item
 GROUP BY evaluation->'ruleset',item->>'rule_id',item->>'status'
 ORDER BY item->>'rule_id',item->>'status') s),'[]') AS rules,
 (SELECT count(*) FROM projected WHERE evaluation_record_id IS NOT NULL) AS checked
""", params({})))

    def detail(self, file_id):
        row = self.repo._query(_BASE + ' SELECT * FROM projected WHERE file_id = %(file)s', params({}) | {'file': file_id})
        if not row:
            return None
        row = _jsonable(row)
        evaluation = row.get('evaluation') or {}
        review = row.get('review') or {}
        # Reuse the authoritative policy in the detail response. SQL policy
        # parity is covered in the integration tests.
        verdict, basis = decide_output(row | {'contextual_review': review})
        checks = evaluation.get('rule_results') or []
        return {
            'file_id': file_id, 'input_id': row['input_id'], 'batch_id': row['batch_id'],
            'request_key': row['request_key'], 'version': row.get('review_record_id') or row.get('evaluation_record_id') or row['input_id'],
            'row': {key: row.get(key) for key in ('vendor','vendor_id','number','date','total','currency','lifecycle','review_status','extraction_route')},
            'salida': {'verdict': verdict, 'basis': basis},
            'attention_required': verdict == 'ESCALAR' if row.get('review_status') in REVIEW_NOT_PURCHASED else review.get('attention_required', True),
            'invoice': row.get('invoice'), 'checks': checks,
            'rule_counts': dict(Counter(item['status'] for item in checks)),
            'evaluation': evaluation, 'review': review or ({'status': row['review_status']} if row.get('review_status') in REVIEW_NOT_PURCHASED else None),
            'evidence': row.get('fields'), 'ruleset': evaluation.get('ruleset'),
            'error': row.get('extraction_error') or row.get('run_error'),
            'lifecycle': [
                {'stage':'received','state':'completed','at':row['received_at']},
                {'stage':'extracted','state':row['extraction_status'],'at':None},
                {'stage':'evaluated','state':'completed' if row.get('evaluation_record_id') else 'not_recorded','at':row['evaluated_at']},
                {'stage':'reviewed','state':row.get('review_status') or 'not_recorded','at':row['reviewed_at']},
                {'stage':'output','state':'derived_from_saved_results' if row.get('evaluation_record_id') else 'not_recorded','at':None},
                {'stage':'resolved','state':'not_recorded','at':None},
                {'stage':'paid','state':'not_recorded','at':None},
            ],
            'pdf_available': bool(row.get('object_key')),
        }

    def pdf(self, file_id):
        row = self.repo._query('''SELECT object_key FROM ingestion.inputs WHERE file_name=%s
 ORDER BY created_at DESC,id DESC LIMIT 1''', (file_id,))
        if not row or not row['object_key']:
            return None
        return self.store.engine.storage.get(row['object_key'])
