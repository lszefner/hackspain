"""Launch the API with locally compiled YAML policy and frozen source lineage."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import yaml

from ingestion.contracts import canonical_bytes, digest
from rules_ingestion import loader, store
from rules_ingestion.build_rules import declared_new_rules
from rules_ingestion.classify import classify_lines
from rules_ingestion.codegen import enrich_new_rules
from rules_ingestion.decision_context import _parse_ruleset
from rules_ingestion.merge import build_merged

ROOT = Path(__file__).resolve().parents[1]


def prepare(sources: Path, profile: Path, output: Path) -> dict[str, str]:
    loaded = loader.load(str(sources))
    profile_bytes = profile.read_bytes()
    classified = classify_lines(loaded.norma_lines, prefer_jev=False, use_llm=False)
    document = store.build_ruleset(
        loaded, classified, yaml.safe_load(profile_bytes),
        generated_at=datetime.now(UTC).isoformat(),
    )
    merged = build_merged(document, declared_new_rules(loaded, classified))
    enrich_new_rules(merged, use_llm=False, gen_python=False)
    rules = canonical_bytes(merged)
    _parse_ruleset(rules)
    # Content-addressed bundles preserve the exact bytes across later YAML edits.
    bundle = output / digest(rules)
    bundle.mkdir(parents=True, exist_ok=True)
    original_sources = {
        'workbook.xlsx': loaded.workbook_bytes,
        'sources.yaml': loaded.source_config_bytes,
        'profile.yaml': profile_bytes,
    }
    for name, content in {**original_sources, 'rules.json': rules}.items():
        (bundle / name).write_bytes(content)
    return {
        'REVISION_RULESET_PATH': str((bundle / 'rules.json').resolve()),
        'REVISION_RULE_SOURCES': canonical_bytes({
            name: str((bundle / name).resolve()) for name in original_sources
        }).decode(),
        'REVISION_SOURCES_PATH': str(sources.resolve()),
        'REVISION_REVIEW_ENABLED': 'false',
    }


def main():
    sources = Path(os.environ.get('REVISION_SOURCES_PATH') or ROOT / 'rules_ingestion/sources.yaml')
    profile = Path(os.environ.get('REVISION_PROFILE_PATH') or ROOT / 'rules_ingestion/profiles/balanced.yaml')
    os.environ.update(prepare(sources, profile, ROOT / 'backend/data/yaml-rules'))
    print(f'Pinned local YAML rules from {profile}; contextual review disabled.', flush=True)
    from backend.server import main as serve
    return serve()


if __name__ == '__main__':
    raise SystemExit(main())
