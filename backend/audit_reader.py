"""Request-local, Postgres-only audit projection; never replay or fetch Storage.

Legacy Storage-addressed JSON is readable when its canonical payload is already
in Postgres. Missing/corrupt payloads fail explicitly, without remote fallback.
Full source verification remains InvoiceDecisionEngine.load's responsibility.
"""
from ingestion.contracts import canonical_bytes, digest
from rules_ingestion.decision_context import ContextError
from rules_ingestion.engine_storage import RECORD_COLUMNS, EngineStore


class AuditReadError(ContextError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class AuditReader:
    def __init__(self, repository):
        self.repository = repository
        self.artifacts = {}
        self.records = {}

    def prefetch(self, artifact_ids):
        missing = {str(value) for value in artifact_ids if value} - self.artifacts.keys()
        if missing:
            self.artifacts.update(dict.fromkeys(missing))
            self.artifacts.update({str(row['id']): row
                                   for row in self.repository.audit_artifacts(sorted(missing))})

    def prepare(self, row):
        ids = [row.get('evaluation_record_id'), row.get('review_record_id')]
        ids = [value for value in ids if value]
        if ids:
            self.records.update(dict.fromkeys(ids))
            for record in self.repository.audit_records(ids):
                self.records[record['record_id']] = record
                self.artifacts[str(record['artifact_id'])] = record['artifact']
        children = [row.get('outcome_artifact_id'), row.get('run_input_artifact_id')]
        for record_id in ids:
            try:
                packet = self.packet(record_id)
            except ContextError:
                continue  # Each flow section reports its own artifact error.
            for key in ('context', 'evaluation', 'review', 'review_config', 'outcome'):
                ref = packet.get(key)
                if isinstance(ref, dict):
                    children.append(ref.get('artifact_id'))
        self.prefetch(children)
        return self

    def json(self, ref):
        artifact_id = str(ref['artifact_id']) if isinstance(ref, dict) else str(ref)
        self.prefetch([artifact_id])
        row = self.artifacts.get(artifact_id)
        if row is None or row.get('payload') is None:
            raise AuditReadError('postgres_audit_missing')
        actual = EngineStore.reference(row)
        if isinstance(ref, dict) and actual != ref:
            raise AuditReadError('postgres_audit_reference_mismatch')
        if row['content_type'] != 'application/json':
            raise AuditReadError('postgres_audit_not_json')
        EngineStore._verify(canonical_bytes(row['payload']), actual)
        return row['payload']

    def packet(self, record_id):
        if record_id not in self.records:
            records = self.repository.audit_records([record_id])
            self.records[record_id] = records[0] if records else None
            if records:
                self.artifacts[str(records[0]['artifact_id'])] = records[0]['artifact']
        record = self.records[record_id]
        if record is None:
            raise AuditReadError('postgres_audit_record_missing')
        packet = self.json(str(record['artifact_id']))
        unsigned = {key: value for key, value in packet.items() if key != 'record_id'}
        artifact = self.artifacts[str(record['artifact_id'])]
        if (artifact['kind'] != 'engine-' + record['kind']
                or 'er_' + digest(canonical_bytes(unsigned)) != record_id
                or packet.get('schema_version') != 'core-engine-record/1'
                or any(str(packet.get(key)) != str(record[key])
                       for key in RECORD_COLUMNS if key != 'artifact_id')):
            raise AuditReadError('postgres_audit_identity_mismatch')
        return packet

    def run_status(self, request_key):
        row = self.repository.get_run(request_key)
        if row is None:
            raise KeyError('run not found')
        header = {'request_key': request_key, 'state': row['state'],
                  'batch_id': str(row['batch_id']) if row['batch_id'] else None}
        if row['result_artifact_id']:
            result = self.json(str(row['result_artifact_id']))
            if any(result.get(key) != value for key, value in header.items()):
                raise AuditReadError('postgres_audit_run_mismatch')
            return result
        return header | {'error': row['error_code']}
