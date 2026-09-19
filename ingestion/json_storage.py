"""Canonical audit JSON in Postgres; original/source bytes in private Storage.

The explicit postgres/ address distinguishes these artifacts from historical
Storage objects. Metadata and exact canonical JSON commit in the same row.
"""
import json

from ingestion.artifact_cache import cached, remember
from ingestion.contracts import canonical_bytes, digest
from ingestion.storage import StorageRef


class PostgresJsonStorage:
    def __init__(self, storage, repository):
        self.storage = storage
        self.repository = repository
        self.compact_json = False

    def __getattr__(self, name):
        return getattr(self.storage, name)

    def object_key(self, data, kind, content_type):
        sha = digest(data)
        key = f'sha256/{sha[:2]}/{sha}/{kind}'
        if (self.compact_json and content_type == 'application/json'
                and kind != 'engine-rule-source' and not kind.startswith('decision-source-')
                and canonical_bytes(json.loads(data)) == data):
            return 'postgres/' + key
        return key

    def put(self, data, kind, content_type='application/octet-stream'):
        key = self.object_key(data, kind, content_type)
        if key.startswith('postgres/'):
            # A proposed reference only. save_artifact's committed payload is
            # the persistence boundary; callers must publish metadata next.
            return StorageRef(digest(data), key, len(data), content_type, kind)
        return self.storage.put(data, kind, content_type)

    def remember_persisted(self, row):
        if row['object_key'].startswith('postgres/'):
            data = canonical_bytes(row['payload'])
            if (digest(data) != row['sha256'] or len(data) != row['byte_size']
                    or row['object_key'] != f"postgres/sha256/{row['sha256'][:2]}/{row['sha256']}/{row['kind']}"):
                raise ValueError('Postgres JSON artifact integrity failure')
            remember(self, row['object_key'], data)

    def get(self, key):
        data = cached(self, key)
        if data is not None:
            return data
        if not key.startswith('postgres/'):
            return self.storage.get(key)
        rows = self.repository.artifacts_by_keys([key])
        if len(rows) != 1 or rows[0]['payload'] is None:
            raise ValueError('Postgres JSON artifact missing')
        self.remember_persisted(rows[0])
        return canonical_bytes(rows[0]['payload'])
