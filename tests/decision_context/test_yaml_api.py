import json
from pathlib import Path

from test_decision_integration import _workbook

from backend.yaml_api import ROOT, prepare


def test_yaml_rules_freeze_sources_and_preserve_disabled_checks(tmp_path):
    sources, workbook = _workbook(tmp_path / 'sources')
    profile = ROOT / 'rules_ingestion/profiles/balanced.yaml'
    config = prepare(sources, profile, tmp_path / 'compiled')
    rules = json.loads(Path(config['REVISION_RULESET_PATH']).read_bytes())
    params = [rule['params'] for rule in rules['rules']]
    assert next(p for p in params if 'check_nif_control_digit' in p)['check_nif_control_digit'] is False
    assert next(p for p in params if 'enforce_payment_terms' in p)['enforce_payment_terms'] is False
    assert config['REVISION_REVIEW_ENABLED'] == 'false'
    frozen = json.loads(config['REVISION_RULE_SOURCES'])
    assert Path(frozen['profile.yaml']).read_bytes() == profile.read_bytes()
    assert Path(frozen['workbook.xlsx']).read_bytes() == workbook.read_bytes()
    assert Path(frozen['sources.yaml']).read_bytes() == sources.read_bytes()
