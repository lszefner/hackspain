"""Durably reuse generated rules by exact inputs; never infer source lineage."""
import os
from pathlib import Path

from ingestion.contracts import canonical_bytes, digest
from rules_ingestion.decision_context import ContextError, _parse_ruleset


def generation_signature(loaded, profile_bytes):
    from rules_ingestion import helmcode

    root = Path(__file__).resolve().parents[1] / 'rules_ingestion'
    return {
        'schema_version': 'rule-generation-input/1',
        'workbook_sha256': digest(loaded.workbook_bytes),
        'mapping_sha256': digest(loaded.source_config_bytes),
        'profile_sha256': digest(profile_bytes),
        'pinned_run': os.environ.get('REVISION_RULESET_RUN_KEY') or None,
        'provider': {'endpoint': helmcode.chat_endpoint(), 'model': helmcode.model(),
                     'authoring_model': helmcode.authoring_model()},
        'implementation': {name: digest((root / name).read_bytes()) for name in (
            'build_rules.py', 'loader.py', 'classify.py', 'catalog.py', 'normalize.py',
            'store.py', 'merge.py', 'codegen.py', 'helmcode.py')},
    }


def reusable_rules(engine, loaded, profile_bytes, *, signature, sources):
    from rules_ingestion.build_rules import build_frozen_rules

    key_hash = digest(canonical_bytes(signature))
    key = 'rules-v1-' + key_hash
    repo = engine.repository
    existing = repo.get_run(key)
    if existing is None:
        if not signature['pinned_run'] and any(not os.environ.get(k) for k in ('JEV_API_KEY', 'HELMCODE_API_KEY')):
            raise ValueError('AI rule generation requires JEV_API_KEY and HELMCODE_API_KEY on a cache miss')
        existing, acquired = repo.claim_run(key, key_hash)
    else:
        acquired = False
    if not acquired:
        if existing['state'] != 'completed' or not existing['result_artifact_id']:
            raise ContextError('rule generation in progress or unknown; supply a verified pinned ruleset')
        packet = engine._json(engine.archive.ref(str(existing['result_artifact_id'])))
        if packet.get('signature') != signature:
            raise ContextError('cached rule generation identity mismatch')
        rules = engine.archive.get(packet['ruleset'])
        audit = engine._json(packet['audit'])
        _parse_ruleset(rules)
        return rules, audit, packet, True

    try:
        # Freeze inputs before invoking paid authoring. The content-derived claim
        # prevents concurrent invoice runs from repeating the same generation.
        source_refs = engine.archive.put_many(
            (s.content, 'engine-rule-source', s.content_type) for s in sources)
        frozen = engine._put_json({'signature': signature, 'sources': [
            {'name': s.name, **ref} for s, ref in zip(sources, source_refs)]}, 'engine-rule-generation-input')
        repo.set_run_input(key, key_hash, frozen['artifact_id'])
        if signature['pinned_run']:
            previous = repo.get_run(signature['pinned_run'])
            if previous is None or previous['state'] != 'completed' or not previous['input_artifact_id']:
                raise ContextError('pinned rules run must be completed')
            original = engine._json(engine.archive.ref(str(previous['input_artifact_id'])))
            expected = {source.name: digest(source.content) for source in sources}
            actual = {source['name']: source['sha256'] for source in original['rule_sources']}
            if actual != expected:
                raise ContextError('pinned rules run sources do not match current workbook, mapping and profile')
            rules = engine.archive.get(original['ruleset'])
            if not isinstance(original.get('rule_generation'), dict):
                raise ContextError('pinned rules run lacks a generation audit')
            audit = engine._json(original['rule_generation'])
        else:
            rules, audit = build_frozen_rules(loaded, profile_bytes,
                                             generated_at=existing['created_at'].isoformat())
        _parse_ruleset(rules)
        refs = engine.archive.put_many([
            (rules, 'decision-source-ruleset', 'application/octet-stream'),
            (canonical_bytes(audit), 'engine-rule-generation', 'application/json'),
        ])
        packet = {'schema_version': 'rule-generation-cache/1', 'signature': signature,
                  'ruleset': refs[0], 'audit': refs[1], 'input': frozen}
        receipt = engine._put_json(packet, 'engine-rule-generation-cache')
        repo.finish_run(key, key_hash, 'completed', receipt['artifact_id'])
        return rules, audit, packet, bool(signature['pinned_run'])
    except BaseException:
        repo.unknown_run(key, key_hash)
        raise
