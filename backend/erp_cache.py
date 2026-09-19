"""Reuse only complete ERP snapshots, for at most 30 seconds."""
from dataclasses import asdict
from datetime import UTC, datetime

from backend.master_data import ErpClient, capture_erp_snapshot
from ingestion.contracts import canonical_bytes, digest
from rules_ingestion.decision_context import SourceSnapshot


def capture_cached_erp(engine, *, captured_at):
    client = ErpClient()
    cache_key = digest(canonical_bytes({'url': client.base_url, 'user': client.usuario}))
    row = engine.repository.recent_erp_snapshot(cache_key)
    if row is not None:
        packet = engine._json(engine.archive.ref(str(row['id'])))
        snapshot = packet['snapshot']
        age = (datetime.now(UTC) - datetime.fromisoformat(snapshot['captured_at'])).total_seconds()
        if (packet.get('cache_key') == cache_key and 0 <= age <= 30
                and snapshot['availability'] == 'available'
                and snapshot['payload'].get('complete') is True):
            snapshot['authoritative_for'] = tuple(snapshot['authoritative_for'])
            return SourceSnapshot(**snapshot), True
    snapshot = capture_erp_snapshot(client, captured_at=captured_at)
    if snapshot.availability == 'available' and snapshot.payload.get('complete') is True:
        engine._put_json({'cache_key': cache_key, 'snapshot': asdict(snapshot)}, 'engine-erp-cache')
    return snapshot, False
