"""Integration: build the full ruleset and assert its invariants + determinism."""
import os
import unittest

from rules_ingestion import loader, store
from rules_ingestion.classify import classify_lines

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CFG = os.path.join(_ROOT, "rules_ingestion", "sources.yaml")
_PROFILE = os.path.join(_ROOT, "rules_ingestion", "rules_v3.yaml")


def _build():
    # prefer_jev=False -> offline lexical tier: hermetic, no network, no key.
    load = loader.load(_CFG)
    classified = classify_lines(load.norma_lines, prefer_jev=False, use_llm=False)
    profile = loader.load_config(_PROFILE)
    return store.build_ruleset(load, classified, profile, generated_at="")


class TestBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rs = _build()
        cls.by_key = {r["canonical"]: r for r in cls.rs["rules"]}

    def test_six_rules(self):
        self.assertEqual(len(self.rs["rules"]), 6)
        self.assertEqual(self.rs["stats"]["flags"], 0)

    def test_precedence(self):
        self.assertEqual(self.rs["precedence"], ["NO_PAGAR", "ESCALAR", "PAGAR"])

    def test_authorization_off_by_profile(self):
        auth = self.by_key["AUTHORIZATION"]
        self.assertFalse(auth["enabled"])
        self.assertEqual(auth["source"], "predefined")     # not in norm text
        self.assertFalse(auth["trace"]["backed_by_master"])

    def test_amount_backed_by_two_norm_sentences(self):
        amount = self.by_key["AMOUNT"]
        cells = [m["cell"] for m in amount["trace"]["matched_from"]]
        self.assertEqual(sorted(cells), ["A3", "A4"])

    def test_on_fail_semantics(self):
        self.assertEqual(self.by_key["DUPLICATES"]["on_fail"], "NO_PAGAR")
        self.assertEqual(self.by_key["VENDOR"]["on_fail"], "ESCALAR")

    def test_every_enabled_rule_has_trace(self):
        for r in self.rs["rules"]:
            self.assertIn("source", r)
            self.assertIn("ruleset_version", r)
            self.assertIn("matched_from", r["trace"])
            if r["trace"]["backed_by_master"]:
                self.assertIsNotNone(r["trace"]["is_rule_conf"])

    def test_determinism_same_input_same_output(self):
        a = _build()
        b = _build()
        self.assertEqual(a["rules"], b["rules"])
        self.assertEqual(a["stats"], b["stats"])


if __name__ == "__main__":
    unittest.main()
