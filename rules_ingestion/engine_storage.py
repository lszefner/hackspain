from __future__ import annotations

import json
import re

from ingestion.contracts import canonical_bytes, digest
from ingestion.storage import PostgresRepository

from .decision_context import ContextError

RECORD_COLUMNS = ('record_id', 'kind', 'input_id', 'parent_record_id', 'artifact_id', 'evaluation_id', 'decision')
RECORD_SELECT = """
SELECT r.*, a.object_key, a.sha256, a.byte_size, a.kind AS artifact_kind
FROM ingestion.engine_records r JOIN ingestion.artifacts a ON a.id = r.artifact_id
WHERE r.record_id = %s
"""


class EngineRepository(PostgresRepository):
    def _query(self, sql, params=()):
        from psycopg.rows import dict_row

        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return dict(row) if (row := cur.fetchone()) is not None else None
        return self._run(op)

    def preflight(self):
        self._query('SELECT request_key FROM ingestion.engine_runs LIMIT 0')
        self._query('SELECT record_id FROM ingestion.engine_records LIMIT 0')
        self._query('SELECT request_key FROM ingestion.engine_review_requests LIMIT 0')

    def extraction(self, input_id: str, interpreter: str):
        return self._query('''
SELECT i.*, r.artifact_id AS outcome_artifact_id, r.status AS outcome_status
FROM ingestion.inputs i JOIN ingestion.input_results r ON r.input_id = i.id
WHERE i.id = %s AND r.interpreter = %s
''', (input_id, interpreter))

    def original(self, object_key: str):
        return self._query("SELECT * FROM ingestion.artifacts WHERE object_key = %s AND kind = 'original'", (object_key,))

    def record(self, record_id: str):
        if not isinstance(record_id, str) or not re.fullmatch(r'er_[a-f0-9]{64}', record_id):
            raise ContextError('invalid engine record id')
        return self._query(RECORD_SELECT, (record_id,))

    def publish(self, values: dict):
        from psycopg.rows import dict_row

        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('''
INSERT INTO ingestion.engine_records
(record_id,kind,input_id,parent_record_id,artifact_id,evaluation_id,decision)
VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (record_id) DO NOTHING
''', tuple(values[key] for key in RECORD_COLUMNS))
                cur.execute(RECORD_SELECT, (values['record_id'],))
                row = cur.fetchone()
                if row is None or any(str(row[key]) != str(values[key]) for key in RECORD_COLUMNS):
                    raise ContextError('conflicting engine record')
                return dict(row)
        return self._run(op)

    def claim_review(self, request_key: str, record_id: str, request_sha256: str):
        from psycopg.rows import dict_row

        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('''
INSERT INTO ingestion.engine_review_requests
(request_key,evaluation_record_id,request_sha256)
VALUES (%s,%s,%s) ON CONFLICT (request_key) DO NOTHING RETURNING *
''', (request_key, record_id, request_sha256))
                row = cur.fetchone()
                claimed = row is not None
                if row is None:
                    cur.execute('SELECT * FROM ingestion.engine_review_requests WHERE request_key = %s', (request_key,))
                    row = cur.fetchone()
                if row is None or row['evaluation_record_id'] != record_id or row['request_sha256'] != request_sha256:
                    raise ContextError('review request key reused with different inputs')
                return dict(row), claimed
        return self._run(op)

    def attach_review_artifact(self, request_key: str, request_sha256: str, artifact_id: str):
        row = self._query('''
UPDATE ingestion.engine_review_requests SET artifact_ids = array_append(artifact_ids, %s::uuid)
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *
''', (artifact_id, request_key, request_sha256))
        if row is None:
            raise ContextError('review request is no longer running')
        return row

    def finish_review(self, request_key: str, request_sha256: str, review_record_id: str):
        row = self._query('''
UPDATE ingestion.engine_review_requests SET state = 'finished', review_record_id = %s, finished_at = now()
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *
''', (review_record_id, request_key, request_sha256))
        if row is None:
            raise ContextError('review request is no longer running')
        return row

    def unknown_review(self, request_key: str, request_sha256: str):
        return self._query('''
UPDATE ingestion.engine_review_requests SET state = 'unknown', error_code = %s, finished_at = now()
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *
''', ('review_interrupted', request_key, request_sha256))

    def claim_run(self, request_key: str, request_sha256: str):
        from psycopg.rows import dict_row

        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('''INSERT INTO ingestion.engine_runs (request_key,request_sha256) VALUES (%s,%s)
ON CONFLICT (request_key) DO NOTHING RETURNING *''', (request_key, request_sha256))
                row = cur.fetchone()
                acquired = row is not None
                if row is None:
                    cur.execute('SELECT * FROM ingestion.engine_runs WHERE request_key = %s', (request_key,))
                    row = cur.fetchone()
                if row is None or row['request_sha256'] != request_sha256:
                    raise ContextError('run request key reused with different inputs')
                return dict(row), acquired
        return self._run(op)

    def get_run(self, request_key: str):
        return self._query('SELECT * FROM ingestion.engine_runs WHERE request_key = %s', (request_key,))

    def set_run_input(self, request_key, request_sha256, artifact_id):
        row = self._query('''UPDATE ingestion.engine_runs SET input_artifact_id = %s
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *''',
                          (artifact_id, request_key, request_sha256))
        if row is None:
            raise ContextError('run is no longer running')
        return row

    def set_run_batch(self, request_key, request_sha256, batch_id):
        row = self._query('''UPDATE ingestion.engine_runs SET batch_id = %s
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' AND batch_id IS NULL RETURNING *''',
                          (batch_id, request_key, request_sha256))
        if row is None:
            raise ContextError('run batch cannot be changed')
        return row

    def finish_run(self, request_key, request_sha256, state, artifact_id):
        if state not in ('completed', 'partial', 'failed'):
            raise ValueError('invalid final run state')
        row = self._query('''UPDATE ingestion.engine_runs SET state = %s, result_artifact_id = %s, finished_at = now()
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *''',
                          (state, artifact_id, request_key, request_sha256))
        if row is None:
            raise ContextError('run is no longer running')
        return row

    def unknown_run(self, request_key, request_sha256):
        return self._query('''UPDATE ingestion.engine_runs SET state = 'unknown', error_code = 'run_interrupted', finished_at = now()
WHERE request_key = %s AND request_sha256 = %s AND state = 'running' RETURNING *''', (request_key, request_sha256))

    def _rows(self, sql, params):
        from psycopg.rows import dict_row

        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        return self._run(op)

    def latest_results(self, file_id=None):
        return self._rows('''
SELECT DISTINCT ON (i.file_name) i.file_name AS file_id, i.id AS input_id, i.batch_id,
 r.status AS extraction_status, r.error AS extraction_error, b.status AS batch_status,
 e.record_id AS evaluation_record_id, e.decision,
 v.record_id AS review_record_id, review_body.payload AS contextual_review,
 run.state AS run_state, run.error_code AS run_error
FROM ingestion.inputs i JOIN ingestion.batches b ON b.id = i.batch_id
LEFT JOIN ingestion.input_results r ON r.input_id = i.id AND r.interpreter = b.config->>'interpreter'
LEFT JOIN LATERAL (
 SELECT er.record_id, er.decision FROM ingestion.engine_records er
 WHERE er.input_id = i.id AND er.kind = 'evaluation' ORDER BY er.created_at DESC, er.record_id DESC LIMIT 1
) e ON true
LEFT JOIN LATERAL (
 SELECT er.record_id, er.artifact_id FROM ingestion.engine_records er
 WHERE er.parent_record_id = e.record_id AND er.kind = 'review' ORDER BY er.created_at DESC, er.record_id DESC LIMIT 1
) v ON true
LEFT JOIN ingestion.artifacts review_packet ON review_packet.id = v.artifact_id
LEFT JOIN ingestion.artifacts review_body ON review_body.id = (review_packet.payload->'review'->>'artifact_id')::uuid
LEFT JOIN ingestion.engine_runs run ON run.batch_id = i.batch_id
WHERE (%s::text IS NULL OR i.file_name = %s)
ORDER BY i.file_name, i.created_at DESC, i.id DESC
''', (file_id, file_id))

    def processed_records(self, exclude_file_id=None):
        return self._rows('''
SELECT DISTINCT ON (er.input_id) er.record_id
FROM ingestion.engine_records er JOIN ingestion.inputs i ON i.id = er.input_id
WHERE er.kind = 'evaluation' AND (%s::text IS NULL OR i.file_name <> %s)
ORDER BY er.input_id, er.created_at DESC, er.record_id DESC
''', (exclude_file_id, exclude_file_id))


class ArchiveError(Exception):
    pass


class EngineStore:
    def __init__(self, repository: EngineRepository, storage):
        self.repository, self.storage = repository, storage

    @staticmethod
    def reference(row: dict) -> dict:
        return {'artifact_id': str(row['id']), **{key: row[key] for key in
                ('object_key', 'sha256', 'byte_size', 'kind', 'content_type')}}

    @staticmethod
    def _verify(data: bytes, ref: dict):
        sha = digest(data)
        if (sha != ref['sha256'] or len(data) != ref['byte_size']
                or ref['object_key'] != f"sha256/{sha[:2]}/{sha}/{ref['kind']}"):
            raise ContextError('engine artifact integrity failure')

    def get(self, ref: dict) -> bytes:
        row = self.repository.get_artifact(ref['artifact_id'])
        if row is None or self.reference(row) != ref:
            raise ContextError('engine artifact metadata mismatch')
        data = self.storage.get(ref['object_key'])
        self._verify(data, ref)
        return data

    def ref(self, artifact_id: str) -> dict:
        row = self.repository.get_artifact(artifact_id)
        if row is None:
            raise ContextError('engine artifact missing')
        ref = self.reference(row)
        self.get(ref)
        return ref

    def put(self, data: bytes, kind: str, content_type='application/json', parents=()) -> dict:
        ref = self.storage.put(data, kind, content_type)
        expected = {'sha256': digest(data), 'byte_size': len(data), 'kind': kind,
                    'object_key': f'sha256/{digest(data)[:2]}/{digest(data)}/{kind}',
                    'content_type': content_type}
        if any(getattr(ref, key) != value for key, value in expected.items()):
            raise ContextError('engine upload reference mismatch')
        self._verify(self.storage.get(ref.object_key), expected)
        payload = None
        if content_type == 'application/json':
            payload = json.loads(data)
        row = self.repository.save_artifact(ref, payload=payload, parent_artifact_ids=parents)
        saved = self.reference(row)
        if any(saved[key] != value for key, value in expected.items()):
            raise ContextError('conflicting engine artifact metadata')
        return saved

    def save_packet(self, unsigned: dict) -> dict:
        packet = {**unsigned, 'record_id': 'er_' + digest(canonical_bytes(unsigned))}
        ref = self.put(canonical_bytes(packet), 'engine-' + packet['kind'])
        self.repository.publish({key: packet[key] for key in RECORD_COLUMNS if key != 'artifact_id'}
                                | {'artifact_id': ref['artifact_id']})
        return packet

    def load_packet(self, record_id: str) -> dict:
        row = self.repository.record(record_id)
        if row is None:
            raise KeyError('engine record not found')
        ref = self.ref(str(row['artifact_id']))
        if ref['kind'] != 'engine-' + row['kind']:
            raise ContextError('engine record artifact kind mismatch')
        packet = json.loads(self.get(ref))
        unsigned = {key: value for key, value in packet.items() if key != 'record_id'}
        if ('er_' + digest(canonical_bytes(unsigned)) != record_id
                or packet.get('schema_version') != 'core-engine-record/1'
                or any(str(packet.get(key)) != str(row[key]) for key in RECORD_COLUMNS if key != 'artifact_id')):
            raise ContextError('engine record identity mismatch')
        return packet
