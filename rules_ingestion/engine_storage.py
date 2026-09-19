from __future__ import annotations

import json
import re

from ingestion.artifact_cache import active_session, cached, forget, remember
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
    def save_artifact(self, *args, **kwargs):
        from .decision_storage import _jsonable
        row = super().save_artifact(*args, **kwargs)
        remember(self, str(row['id']), canonical_bytes(_jsonable(row)))
        return row

    def get_artifact(self, artifact_id):
        value = cached(self, str(artifact_id))
        return json.loads(value) if value is not None else super().get_artifact(artifact_id)

    def extraction_trace(self, input_id, *, api_projection=False):
        attempt = ("to_jsonb(a) || jsonb_build_object('latency_seconds', a.latency_seconds::text)"
                   if api_projection else 'to_jsonb(a)')
        return self._rows(f'''SELECT j.*, COALESCE((
SELECT jsonb_agg({attempt} ORDER BY a.attempt_number) FROM ingestion.attempts a WHERE a.job_id = j.id
), '[]'::jsonb) AS attempts FROM ingestion.jobs j WHERE j.input_id = %s
ORDER BY j.created_at, j.id''', (input_id,))

    def audit_artifacts(self, artifact_ids):
        return self._rows('''SELECT id, sha256, kind, object_key, content_type, byte_size, payload
FROM ingestion.artifacts WHERE id = ANY(%s::uuid[])''', (list(artifact_ids),))

    def audit_records(self, record_ids):
        return self._rows('''SELECT r.*, to_jsonb(a) AS artifact
FROM ingestion.engine_records r LEFT JOIN ingestion.artifacts a ON a.id = r.artifact_id
WHERE r.record_id = ANY(%s::text[])''', (list(record_ids),))

    def record_local_job(self, *, batch_id, input_id, stage, source_hash, provider,
                         model, config, settings, artifact_id, status, latency_seconds):
        """Publish completed pure CPU work and its trace in one transaction.

        No external effect is possible in this path; paid jobs retain leases,
        pre-call attempts, recovery and unknown-outcome protection.
        """
        if (stage, provider) not in (('reading', 'native-text'), ('interpretation', 'deterministic')):
            raise ValueError('only native deterministic stages may publish local jobs')
        work_key = 'local-v1-' + digest(canonical_bytes({
            'input_id': str(input_id), 'source_hash': source_hash, 'stage': stage,
            'provider': provider, 'model': model, 'config': config, 'settings': settings}))
        row = self._query('''WITH stored AS (
 INSERT INTO ingestion.jobs
 (batch_id,input_id,stage,input_artifact_hash,provider,model,config_version,prompt_version,
  adapter_version,schema_hash,settings,work_key,state,max_attempts,attempt_count,artifact_id)
 SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,1,1,id
 FROM ingestion.artifacts WHERE id = %s AND verified_at IS NOT NULL
 ON CONFLICT (work_key) DO UPDATE SET updated_at = now() RETURNING *
), attempt AS (
 INSERT INTO ingestion.attempts (job_id,attempt_number,status,finished_at,latency_seconds,usage)
 SELECT id,1,state,now(),%s,'{}'::jsonb FROM stored
 ON CONFLICT (job_id,attempt_number) DO NOTHING
) SELECT * FROM stored''', (
            batch_id, input_id, stage, source_hash, provider, model,
            config['version'], config['version'], config['version'],
            config['schema_hashes']['reading' if stage == 'reading' else 'invoice'],
            canonical_bytes(settings).decode(), work_key, status, artifact_id, latency_seconds))
        if row is None:
            raise ValueError('local job artifact is missing or unverified')
        return row

    def recent_erp_snapshot(self, cache_key):
        return self._query('''SELECT * FROM ingestion.artifacts
WHERE kind = 'engine-erp-cache' AND payload->>'cache_key' = %s
AND created_at >= now() - interval '30 seconds'
ORDER BY created_at DESC LIMIT 1''', (cache_key,))

    def save_artifacts(self, values):
        """One metadata round trip after all independent uploads verify."""
        return self._rows('''
INSERT INTO ingestion.artifacts
(sha256,kind,object_key,content_type,byte_size,payload,parent_artifact_ids,verified_at)
SELECT sha256,kind,object_key,content_type,byte_size,payload,parent_artifact_ids,now()
FROM jsonb_to_recordset(%s::jsonb) AS x(
 sha256 text,kind text,object_key text,content_type text,byte_size bigint,
 payload jsonb,parent_artifact_ids uuid[])
ON CONFLICT (sha256,kind) DO UPDATE SET updated_at = now()
RETURNING *
''', (canonical_bytes(values).decode(),))

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
SELECT i.*, r.artifact_id AS outcome_artifact_id, r.status AS outcome_status,
 to_jsonb(original) AS original_artifact, to_jsonb(outcome) AS outcome_artifact
FROM ingestion.inputs i JOIN ingestion.input_results r ON r.input_id = i.id
LEFT JOIN ingestion.artifacts original ON original.object_key = i.object_key AND original.kind = 'original'
LEFT JOIN ingestion.artifacts outcome ON outcome.id = r.artifact_id
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

    def latest_results(self, file_id=None, *, summary=False):
        review_projection = ("jsonb_build_object('status', review_body.payload->'status', "
                             "'attention_required', review_body.payload->'attention_required', "
                             "'error', review_body.payload->'error')" if summary else 'review_body.payload')
        return self._rows(f'''
WITH latest_inputs AS MATERIALIZED (
 SELECT DISTINCT ON (file_name) id, batch_id, file_name, content_hash, size_bytes, created_at
 FROM ingestion.inputs WHERE (%s::text IS NULL OR file_name = %s)
 ORDER BY file_name, created_at DESC, id DESC
)
SELECT i.file_name AS file_id, i.id AS input_id, i.batch_id,
 i.content_hash, i.size_bytes, i.created_at AS received_at,
 r.status AS extraction_status, r.error AS extraction_error, b.status AS batch_status,
 r.artifact_id AS outcome_artifact_id,
 e.record_id AS evaluation_record_id, e.decision,
 v.record_id AS review_record_id, {review_projection} AS contextual_review,
 run.state AS run_state, run.error_code AS run_error, run.request_key,
 run.input_artifact_id AS run_input_artifact_id,
 run_input.payload->'review_policy' AS review_policy
FROM latest_inputs i JOIN ingestion.batches b ON b.id = i.batch_id
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
LEFT JOIN ingestion.artifacts run_input ON run_input.id = run.input_artifact_id
ORDER BY i.file_name
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

    def put_many(self, items):
        """Persist independent artifacts concurrently; publish packets afterwards."""
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context

        items = list(items)
        if not items:
            return []
        specs = {}
        order = []
        for item in items:
            data, kind, *rest = item
            content_type = rest[0] if rest else 'application/json'
            parents = list(rest[1]) if len(rest) > 1 else []
            sha = digest(data)
            key = (self.storage.object_key(data, kind, content_type) if hasattr(self.storage, 'object_key')
                   else f'sha256/{sha[:2]}/{sha}/{kind}')
            spec = {'sha256': sha, 'object_key': key, 'kind': kind, 'byte_size': len(data),
                    'content_type': content_type, 'parent_artifact_ids': parents,
                    'payload': json.loads(data) if content_type == 'application/json' else None}
            if key in specs and specs[key][1] != spec:
                raise ContextError('conflicting artifact metadata in batch')
            specs[key] = (data, spec)
            order.append(key)
        alternatives = {key: key.removeprefix('postgres/') if key.startswith('postgres/')
                        else 'postgres/' + key for key in specs}
        candidates = list(specs) + list(alternatives.values())
        existing = {row['object_key']: row for row in self.repository.artifacts_by_keys(candidates)}
        for key in list(specs):
            legacy = alternatives[key]
            if key not in existing and legacy in existing:
                data, spec = specs.pop(key)
                specs[legacy] = (data, spec | {'object_key': legacy})
                order = [legacy if value == key else value for value in order]

        def persist(key):
            data, spec = specs[key]
            if key in existing:
                ref = self.reference(existing[key])
                if any(ref[k] != spec[k] for k in ('sha256', 'object_key', 'kind', 'byte_size', 'content_type')):
                    raise ContextError('conflicting engine artifact metadata')
                if active_session() and existing[key].get('verified_at') is not None:
                    # The caller supplies the exact source bytes, and the
                    # immutable object has already been durably verified.
                    # Avoid re-downloading shared workbooks/schemas per invoice.
                    self._verify(data, ref)
                    remember(self.storage, key, data)
                else:
                    self._verify(self.storage.get(key), ref)
                return
            ref = self.storage.put(data, spec['kind'], spec['content_type'])
            if any(getattr(ref, k) != spec[k] for k in ('sha256', 'object_key', 'kind', 'byte_size', 'content_type')):
                raise ContextError('engine upload reference mismatch')
            if not key.startswith('postgres/'):
                self._verify(self.storage.get(key), spec)

        with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
            futures = [pool.submit(copy_context().run, persist, key) for key in specs]
            for future in futures:
                future.result()
        missing = [spec for key, (_, spec) in specs.items() if key not in existing]
        if missing:
            for row in self.repository.save_artifacts(missing):
                existing[row['object_key']] = row
        result = []
        for key in order:
            ref = self.reference(existing[key])
            if any(ref[k] != specs[key][1][k] for k in ('sha256', 'object_key', 'kind', 'byte_size', 'content_type')):
                raise ContextError('conflicting engine artifact metadata')
            self._remember_row(existing[key])
            if hasattr(self.storage, 'remember_persisted'):
                self.storage.remember_persisted(existing[key])
            result.append(ref)
        return result

    def _remember_row(self, row):
        from .decision_storage import _jsonable
        remember(self, str(row['id']), canonical_bytes(_jsonable(row)))

    def _row(self, artifact_id):
        value = cached(self, str(artifact_id))
        if value is not None:
            return json.loads(value)
        row = self.repository.get_artifact(artifact_id)
        if row is not None:
            self._remember_row(row)
        return row

    @staticmethod
    def reference(row: dict) -> dict:
        return {'artifact_id': str(row['id']), **{key: row[key] for key in
                ('object_key', 'sha256', 'byte_size', 'kind', 'content_type')}}

    @staticmethod
    def _verify(data: bytes, ref: dict):
        sha = digest(data)
        if (sha != ref['sha256'] or len(data) != ref['byte_size']
                or ref['object_key'].removeprefix('postgres/') != f"sha256/{sha[:2]}/{sha}/{ref['kind']}"):
            raise ContextError('engine artifact integrity failure')

    def _data(self, row, ref, *, fresh=False):
        if fresh:
            forget(self.storage, ref['object_key'])
        elif active_session() and row['content_type'] == 'application/json' and row.get('payload') is not None:
            # Postgres stores these exact canonical bytes as well as their
            # Storage copy. Verify the hash before using the durable projection.
            data = canonical_bytes(row['payload'])
            if digest(data) == ref['sha256']:
                self._verify(data, ref)
                return data
            # Original JSON sources can contain significant noncanonical bytes
            # (whitespace/number spelling). Their exact Storage copy is authority.
        data = self.storage.get(ref['object_key'])
        self._verify(data, ref)
        return data

    def get(self, ref: dict, *, fresh=False) -> bytes:
        row = self._row(ref['artifact_id'])
        if row is None or self.reference(row) != ref:
            raise ContextError('engine artifact metadata mismatch')
        return self._data(row, ref, fresh=fresh)

    def ref(self, artifact_id: str) -> dict:
        row = self._row(artifact_id)
        if row is None:
            raise ContextError('engine artifact missing')
        ref = self.reference(row)
        self._data(row, ref)
        return ref

    def put(self, data: bytes, kind: str, content_type='application/json', parents=()) -> dict:
        ref = self.storage.put(data, kind, content_type)
        expected = {'sha256': digest(data), 'byte_size': len(data), 'kind': kind,
                    'object_key': (self.storage.object_key(data, kind, content_type)
                                   if hasattr(self.storage, 'object_key') else
                                   f'sha256/{digest(data)[:2]}/{digest(data)}/{kind}'),
                    'content_type': content_type}
        if any(getattr(ref, key) != value for key, value in expected.items()):
            raise ContextError('engine upload reference mismatch')
        if not ref.object_key.startswith('postgres/'):
            self._verify(self.storage.get(ref.object_key), expected)
        payload = None
        if content_type == 'application/json':
            payload = json.loads(data)
        row = self.repository.save_artifact(ref, payload=payload, parent_artifact_ids=parents)
        saved = self.reference(row)
        if saved['object_key'].removeprefix('postgres/') == expected['object_key'].removeprefix('postgres/'):
            expected['object_key'] = saved['object_key']
        if any(saved[key] != value for key, value in expected.items()):
            raise ContextError('conflicting engine artifact metadata')
        self._remember_row(row)
        if hasattr(self.storage, 'remember_persisted'):
            self.storage.remember_persisted(row)
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
