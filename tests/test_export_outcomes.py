"""Deliverable export policy: decide_output / build_rows / write_jsonl."""
from __future__ import annotations

import json

from backend.export_outcomes import build_rows, decide_output, write_jsonl


def _row(**kw):
    row = {"evaluation_record_id": "er_" + "1" * 64,
           "review_record_id": "er_" + "2" * 64,
           "extraction_status": "completed",
           "decision": "PAGAR",
           "review_status": "COMPLETED",
           "attention_required": False,
           "contextual_review": {"status": "COMPLETED",
                                 "rule_reviews": [], "findings": []}}
    row.update(kw)
    return row


class TestDecideOutput:
    def test_no_row_or_no_evaluation_escalates(self):
        assert decide_output(None) == ("ESCALAR", "no_evaluation")
        assert decide_output({}) == ("ESCALAR", "no_evaluation")
        assert decide_output(_row(evaluation_record_id=None)) == (
            "ESCALAR", "no_evaluation")

    def test_failed_extraction_escalates(self):
        assert decide_output(_row(extraction_status="failed")) == (
            "ESCALAR", "no_evaluation")

    def test_evaluator_rejections_pass_through(self):
        assert decide_output(_row(decision="NO_PAGAR")) == (
            "NO_PAGAR", "evaluator")
        assert decide_output(_row(decision="ESCALAR")) == (
            "ESCALAR", "evaluator")

    def test_pagar_confirmed_by_clean_review(self):
        assert decide_output(_row()) == (
            "PAGAR", "evaluator_confirmed_by_review")
        assert decide_output(_row(review_status="INCOMPLETE")) == (
            "PAGAR", "evaluator_confirmed_by_review")

    def test_challenged_rule_escalates(self):
        review = {"status": "COMPLETED",
                  "rule_reviews": [{"rule_id": "A1", "assessment": "CHALLENGED"}],
                  "findings": []}
        assert decide_output(_row(contextual_review=review)) == (
            "ESCALAR", "review_challenged")

    def test_blocking_finding_escalates(self):
        review = {"status": "COMPLETED", "rule_reviews": [],
                  "findings": [{"kind": "EVIDENCE_MISMATCH",
                                "severity": "blocking"}]}
        assert decide_output(_row(contextual_review=review)) == (
            "ESCALAR", "review_challenged")

    def test_blocking_review_limitation_does_not_challenge(self):
        review = {"status": "INCOMPLETE", "rule_reviews": [],
                  "findings": [{"kind": "REVIEW_LIMITATION",
                                "severity": "blocking"}]}
        assert decide_output(_row(contextual_review=review,
                                review_status="INCOMPLETE")) == (
            "PAGAR", "evaluator_confirmed_by_review")

    def test_missing_or_failed_review_escalates(self):
        assert decide_output(_row(review_status=None,
                                  contextual_review=None)) == (
            "ESCALAR", "review_unavailable")
        assert decide_output(_row(review_status="FAILED",
                                  contextual_review={"status": "FAILED"})) == (
            "ESCALAR", "review_unavailable")


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows
        self.engine = None

    def all(self):
        return dict(self._rows)

    def get(self, file_id):
        return self._rows.get(file_id)


def test_build_rows_key_order_and_not_processed(tmp_path):
    store = _FakeStore({"a.pdf": _row(), "b.pdf": _row(decision="ESCALAR")})
    rows = build_rows(store, ["a.pdf", "b.pdf", "c.pdf"])
    assert [row["output"] for row in rows] == ["PAGAR", "ESCALAR", "ESCALAR"]
    assert rows[2]["trace"]["basis"] == "not_processed"
    for row in rows:
        assert list(row) == ["invoice", "output", "file_id", "result", "trace"]
    out = tmp_path / "out.jsonl"
    write_jsonl(rows, out)
    loaded = [json.loads(line) for line in out.read_text().splitlines()]
    assert loaded == rows
    assert loaded[0]["trace"]["evaluator_decision"] == "PAGAR"


def test_build_rows_uses_store_all_when_listed(tmp_path):
    store = _FakeStore({"a.pdf": _row(decision="NO_PAGAR")})
    rows = build_rows(store, sorted(store.all()))
    assert rows[0]["output"] == "NO_PAGAR"
    assert rows[0]["trace"]["basis"] == "evaluator"
