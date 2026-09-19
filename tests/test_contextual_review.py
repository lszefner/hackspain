import asyncio
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from ingestion.contextual_prompt import SYSTEM_PROMPT
from ingestion.contracts import canonical_bytes, digest
from rules_ingestion.contextual_contracts import (
    EVALUATION_SCHEMA,
    RESPONSE_SCHEMA,
    REVIEW_SCHEMA,
    ReviewInputError,
    pointer_get,
)
from rules_ingestion.contextual_fixtures import (
    CAPTURED_AT,
    FIXTURES,
    REVIEWED_AT,
    FixtureProvider,
    build_fixture,
    evaluation_id,
)
from rules_ingestion.contextual_review import (
    ContextView,
    DecisionContextV1Adapter,
    ProviderReply,
    ReviewLimits,
    review_evaluation,
)
from rules_ingestion.contextual_review_cli import main
from rules_ingestion.decision_context import SourceSnapshot


async def run(fixture, provider=None, **kwargs):
    return await review_evaluation(fixture.evaluation, fixture.context, fixture.artifacts,
                                   provider or FixtureProvider(fixture.response), reviewed_at=REVIEWED_AT, **kwargs)


@pytest.mark.parametrize("schema", [EVALUATION_SCHEMA, RESPONSE_SCHEMA, REVIEW_SCHEMA])
def test_schemas_are_valid(schema):
    Draft202012Validator.check_schema(schema)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,decision,status,attention", [
    ("clean-approval", "PAGAR", "COMPLETED", False),
    ("bank-mismatch", "ESCALAR", "COMPLETED", True),
    ("database-duplicate", "NO_PAGAR", "COMPLETED", True),
    ("erp-paid-with-blockers", "NO_PAGAR", "INCOMPLETE", True),
    ("unsupported-rule", "ESCALAR", "INCOMPLETE", True),
    ("new-bank-account", "PAGAR", "COMPLETED", True),
])
async def test_six_fixtures_preserve_original(name, decision, status, attention):
    fixture = build_fixture(name)
    original = deepcopy(fixture)
    provider = FixtureProvider(fixture.response)
    review = await run(fixture, provider)
    assert provider.calls == 1
    assert fixture == original
    assert review["original_preliminary_decision"] == decision
    assert review["status"] == status
    assert review["attention_required"] is attention
    assert review["payment_authorized"] is False
    assert review["evaluation_id"] == fixture.evaluation["evaluation_id"]
    assert review["context_id"] == fixture.context["context_id"]
    assert review["review_id"] == "rv_" + digest(canonical_bytes({k: v for k, v in review.items() if k != "review_id"}))
    assert [r["rule_id"] for r in review["rule_reviews"]] == fixture.evaluation["completeness"]["enabled_rule_ids"]
    review["rule_reviews"][0]["explanation"] = "edited downstream"
    assert fixture == original


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["submitted", "processed"])
async def test_duplicate_rejection_does_not_claim_payment(state):
    fixture = build_fixture("database-duplicate", duplicate_status=state)
    review = await run(fixture)
    assert review["original_preliminary_decision"] == "NO_PAGAR"
    assert review["attention_required"]
    finding = review["findings"][0]
    assert finding["code"] == "CONFIRMED_DATABASE_DUPLICATE"
    assert finding["severity"] == "blocking"
    assert "no payment evidence is asserted" in finding["explanation"]
    assert "already paid" not in canonical_bytes(review).decode()
    assert fixture.context["preflight"]["execution_status"] == "blocked"
    assert review["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_new_account_evidence_challenges_pass_without_authorization():
    fixture = build_fixture("new-bank-account")
    review = await run(fixture)
    assert fixture.evaluation["preliminary_decision"] == "PAGAR"
    assert review["rule_reviews"][0]["assessment"] == "CHALLENGED"
    assert review["findings"][0]["kind"] == "AUTHORIZATION_GAP"
    assert {c["source"] for c in review["findings"][0]["evidence"]} == {"invoice", "reading"}
    assert not review["payment_authorized"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["hash", "context_hash", "context_id", "context_version", "date", "artifact",
                                      "missing_artifact", "ruleset_version", "ruleset_hash", "missing_rule",
                                      "duplicate_rule", "fact_pointer", "fact_value", "unknown_field", "trace",
                                      "unknown_source", "false_accounting", "approval", "complete"])
async def test_invalid_inputs_fail_before_provider(mutation):
    fixture = build_fixture("clean-approval")
    ev = fixture.evaluation
    rr = ev["rule_results"][0]
    if mutation == "hash":
        ev["preliminary_decision"] = "ESCALAR"
    elif mutation == "context_hash":
        ev["context_sha256"] = "0" * 64
    elif mutation == "context_id":
        ev["context_id"] = "different"
    elif mutation == "context_version":
        ev["context_schema_version"] = "different/1"
    elif mutation == "date":
        ev["evaluation_date"] = "2026-09-18"
    elif mutation in {"artifact", "missing_artifact"}:
        key = fixture.context["sources"]["invoice"]["artifact_ref"]
        if mutation == "artifact":
            fixture.artifacts[key] = b"{}"
        else:
            del fixture.artifacts[key]
    elif mutation == "ruleset_version":
        ev["ruleset"]["ruleset_version"] = "wrong"
    elif mutation == "ruleset_hash":
        ev["ruleset"]["sha256"] = "0" * 64
    elif mutation == "missing_rule":
        ev["rule_results"] = []
    elif mutation == "duplicate_rule":
        ev["rule_results"].append(deepcopy(rr))
    elif mutation == "fact_pointer":
        rr["inputs"][0]["fact_pointer"] = "/fields/supplier.id"
    elif mutation == "fact_value":
        rr["inputs"][0]["value"] = "fabricated"
    elif mutation == "unknown_field":
        rr["evidence_refs"] = [{"field": "not.a.field"}]
    elif mutation == "trace":
        rr["trace"][0]["node"] = "/rules/100/condition"
    elif mutation == "unknown_source":
        rr["evidence_refs"] = [{"source": "invented", "pointer": ""}]
    elif mutation == "false_accounting":
        ev["completeness"]["all_enabled_accounted"] = False
    elif mutation == "approval":
        ev["preliminary_decision"] = "NO_PAGAR"
    elif mutation == "complete":
        rr["complete"] = False
    if mutation != "hash":
        evaluation_id(ev)
    provider = FixtureProvider(fixture.response)
    with pytest.raises(ReviewInputError):
        await run(fixture, provider)
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_missed_duplicate_is_blocking_without_rewriting_first_pass():
    fixture = build_fixture("database-duplicate")
    duplicate = fixture.evaluation["rule_results"][1]
    duplicate.update(status="PASS", compliance="PASS", applied_consequence="PAGAR",
                     reason_codes=["SYNTHETIC_MISSED_DUPLICATE"], explanation="Synthetic first pass missed the duplicate.")
    fixture.evaluation["preliminary_decision"] = "PAGAR"
    fixture.evaluation["completeness"]["approval_eligible"] = True
    evaluation_id(fixture.evaluation)
    fixture.response["rule_reviews"][1]["assessment"] = "CHALLENGED"
    review = await run(fixture)
    assert review["original_preliminary_decision"] == "PAGAR"
    assert fixture.evaluation["preliminary_decision"] == "PAGAR"
    assert review["findings"][0]["code"] == "CONFIRMED_DATABASE_DUPLICATE"
    assert review["findings"][0]["severity"] == "blocking"
    assert review["attention_required"] and not review["payment_authorized"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quote", "pointer", "source", "rule", "decision", "authorization",
                                      "missing_rule", "duplicate_rule", "citation_coverage", "empty_citation",
                                      "challenge_without_finding", "bad_finding_rule", "no_finding_evidence"])
async def test_invalid_model_output_fails_closed(mutation):
    fixture = build_fixture("clean-approval")
    response = deepcopy(fixture.response)
    rr = response["rule_reviews"][0]
    if mutation in {"quote", "pointer", "source"}:
        rr["evidence"][0][mutation] = "fabricated"
    elif mutation == "rule":
        rr["rule_id"] = "invented"
    elif mutation == "decision":
        response["decision"] = "PAGAR"
    elif mutation == "authorization":
        response["payment_authorized"] = True
    elif mutation == "missing_rule":
        response["rule_reviews"] = []
    elif mutation == "duplicate_rule":
        response["rule_reviews"].append(deepcopy(rr))
    elif mutation == "citation_coverage":
        response["reviewed_sources"].remove("invoice")
    elif mutation == "empty_citation":
        rr["evidence"] = []
    elif mutation == "challenge_without_finding":
        rr["assessment"] = "CHALLENGED"
    else:
        response["findings"] = [{"code": "CONCERN", "kind": "MISSED_DETAIL", "severity": "blocking",
                                 "rule_ids": ["wrong"] if mutation == "bad_finding_rule" else [rr["rule_id"]],
                                 "explanation": "Something needs review.",
                                 "evidence": rr["evidence"] if mutation == "bad_finding_rule" else []}]
    review = await run(fixture, FixtureProvider(response))
    assert review["status"] == "FAILED"
    assert review["error"]["code"] == "invalid_response"
    assert review["findings"] == []
    assert review["attention_required"] and not review["payment_authorized"]


@pytest.mark.asyncio
async def test_unsupported_cannot_be_completed_by_model():
    fixture = build_fixture("unsupported-rule")
    fixture.response["rule_reviews"][0]["assessment"] = "SUPPORTED"
    assert (await run(fixture))["status"] == "FAILED"


@pytest.mark.asyncio
async def test_missing_source_coverage_is_incomplete():
    fixture = build_fixture("clean-approval")
    fixture.response["reviewed_sources"].remove("checks")
    review = await run(fixture)
    assert review["status"] == "INCOMPLETE"
    assert review["coverage"]["unreviewed_sources"] == ["checks"]


@pytest.mark.asyncio
@pytest.mark.parametrize("availability,kind,payload", [
    ("partial", "history", {"kind": "processed", "records": [], "complete": False}),
    ("unavailable", "history", None),
    ("available", "master_workbook", b"synthetic-binary-workbook"),
])
async def test_incomplete_sources_are_explicit(availability, kind, payload):
    source = SourceSnapshot(kind=kind, payload=payload, captured_at=CAPTURED_AT,
                            asserted_by="fixture-operator", authoritative_for=(), scope="synthetic", availability=availability)
    fixture = build_fixture("clean-approval", source_overrides={"extra": source})
    review = await run(fixture)
    assert review["status"] == "INCOMPLETE"
    assert review["attention_required"]
    assert any("extra" in text for text in review["limitations"])


@pytest.mark.asyncio
async def test_timeout_failure_does_not_weaken_rejection_or_leak(caplog):
    fixture = build_fixture("database-duplicate")

    class Broken(FixtureProvider):
        async def review(self, request):
            raise RuntimeError("secret-key-do-not-log")

    class Slow(FixtureProvider):
        async def review(self, request):
            await asyncio.sleep(10)

    for provider, code in ((Broken({}), "provider_error"), (Slow({}), "timeout")):
        with caplog.at_level("INFO"):
            review = await run(fixture, provider, limits=ReviewLimits(timeout_seconds=0.01))
        assert review["status"] == "FAILED"
        assert review["error"]["code"] == code
        assert review["original_preliminary_decision"] == "NO_PAGAR"
        assert "secret-key-do-not-log" not in canonical_bytes(review).decode() + caplog.text
        assert caplog.records[-1].evaluation_id == fixture.evaluation["evaluation_id"]


@pytest.mark.asyncio
async def test_input_output_limits_and_cancellation():
    fixture = build_fixture("clean-approval")
    provider = FixtureProvider(fixture.response)
    result = await run(fixture, provider, limits=ReviewLimits(max_input_bytes=1))
    assert result["error"]["code"] == "input_too_large"
    assert provider.calls == 0
    result = await run(fixture, provider, limits=ReviewLimits(max_output_bytes=1))
    assert result["error"]["code"] == "output_too_large"

    class Cancelled(FixtureProvider):
        async def review(self, request):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run(fixture, Cancelled({}))


@pytest.mark.asyncio
async def test_provider_cannot_mutate_frozen_inputs():
    fixture = build_fixture("clean-approval")
    saved = deepcopy(fixture)

    class Mutating(FixtureProvider):
        async def review(self, request):
            assert request["system_prompt"] == SYSTEM_PROMPT
            assert "untrusted" in SYSTEM_PROMPT
            assert request["evaluation"]["rule_results"][0]["reason_codes"] == ["VENDOR_MATCH"]
            assert set(request["sources"]) == set(fixture.response["reviewed_sources"])
            request["evaluation"]["preliminary_decision"] = "NO_PAGAR"
            request["context"].clear()
            request["sources"].clear()
            return ProviderReply(deepcopy(self.response), self.model)

    review = await run(fixture, Mutating(fixture.response))
    assert review["status"] == "COMPLETED"
    assert fixture == saved


@pytest.mark.asyncio
async def test_alternate_context_version_uses_adapter_not_evaluator():
    fixture = build_fixture("clean-approval")
    original_view = DecisionContextV1Adapter().inspect(fixture.context, fixture.artifacts)
    context = {"schema_version": "synthetic-context/2", "context_id": "synthetic-context-2",
               "facts": deepcopy(original_view.fields)}
    evaluation = deepcopy(fixture.evaluation)
    evaluation.update(context_id=context["context_id"], context_schema_version=context["schema_version"],
                      context_sha256=digest(canonical_bytes(context)))
    for item in evaluation["rule_results"][0]["inputs"]:
        item["fact_pointer"] = "/facts/" + item["field"]
    evaluation_id(evaluation)

    class AlternateAdapter:
        def inspect(self, context, artifacts):
            return ContextView(context["context_id"], context["schema_version"], "2026-09-19",
                               original_view.sources, context["facts"],
                               {f: "/facts/" + f for f in context["facts"]},
                               original_view.ruleset, original_view.rule_bindings)

    with pytest.raises(ReviewInputError, match="unsupported_context_version"):
        await review_evaluation(evaluation, context, fixture.artifacts, FixtureProvider(fixture.response), reviewed_at=REVIEWED_AT)
    review = await review_evaluation(evaluation, context, fixture.artifacts, FixtureProvider(fixture.response),
                                     reviewed_at=REVIEWED_AT, adapter=AlternateAdapter())
    assert review["status"] == "COMPLETED"
    assert review["context_schema_version"] == "synthetic-context/2"


@pytest.mark.asyncio
async def test_evaluation_level_incomplete_is_not_inferred_complete():
    fixture = build_fixture("clean-approval")
    fixture.evaluation["completeness"].update(evaluation_complete=False, approval_eligible=False)
    evaluation_id(fixture.evaluation)
    assert (await run(fixture))["status"] == "INCOMPLETE"


@pytest.mark.parametrize("pointer", ["/a/~2", "/a/-1", "/a/01", "/a/+1", "/a/-", "/a/2", "a"])
def test_invalid_pointers(pointer):
    with pytest.raises(ReviewInputError):
        pointer_get({"a": ["first"]}, pointer)


def test_escaped_and_root_pointers():
    value = {"a/b~c": ["first"]}
    assert pointer_get(value, "/a~1b~0c/0") == "first"
    assert pointer_get(value, "") == value


@pytest.mark.parametrize("name", FIXTURES)
def test_offline_cli_never_overwrites(tmp_path, capsys, name):
    target = tmp_path / "review.json"
    assert main(["--fixture", name, "--output", str(target)]) == 0
    original = target.read_bytes()
    bundle = json.loads(original)
    assert bundle["synthetic"] is True
    assert bundle["evaluation"]["evaluation_id"] == bundle["review"]["evaluation_id"]
    assert main(["--fixture", name, "--output", str(target)]) == 1
    assert target.read_bytes() == original
    assert "fixture_input_or_output_error" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["database-duplicate", "new-bank-account"])
async def test_hard_findings_cannot_be_downgraded_to_info(name):
    fixture = build_fixture(name)
    fixture.response["findings"][0]["severity"] = "info"
    for item in fixture.response["rule_reviews"]:
        item["assessment"] = "SUPPORTED"
    review = await run(fixture)
    assert review["status"] == "FAILED"
    assert review["attention_required"]


@pytest.mark.asyncio
async def test_model_limitation_cannot_be_reported_as_complete():
    fixture = build_fixture("clean-approval")
    fixture.response["findings"].append({
        "code": "UNABLE_TO_ASSESS", "kind": "REVIEW_LIMITATION", "severity": "info",
        "rule_ids": [], "explanation": "Some source text could not be assessed.", "evidence": [],
    })
    review = await run(fixture)
    assert review["status"] == "INCOMPLETE"
    assert review["attention_required"]


@pytest.mark.asyncio
async def test_input_binary_root_reference_is_preserved_as_limitation():
    source = SourceSnapshot(kind="master_workbook", payload=b"synthetic workbook", captured_at=CAPTURED_AT,
                            asserted_by="fixture", authoritative_for=(), scope="synthetic")
    fixture = build_fixture("clean-approval", source_overrides={"workbook": source})
    fixture.evaluation["rule_results"][0]["evidence_refs"].append({"source": "workbook", "pointer": ""})
    evaluation_id(fixture.evaluation)
    review = await run(fixture)
    assert review["status"] == "INCOMPLETE"
    assert review["coverage"]["unreviewable_sources"] == ["workbook"]


@pytest.mark.asyncio
async def test_nullable_on_fail_in_unexecuted_rule_is_accepted():
    fixture = build_fixture("unsupported-rule")
    fixture.evaluation["rule_results"][0]["on_fail"] = None
    evaluation_id(fixture.evaluation)
    assert (await run(fixture))["status"] == "INCOMPLETE"


@pytest.mark.asyncio
async def test_reordered_rules_are_rejected():
    fixture = build_fixture("database-duplicate")
    fixture.evaluation["rule_results"].reverse()
    evaluation_id(fixture.evaluation)
    provider = FixtureProvider(fixture.response)
    with pytest.raises(ReviewInputError, match="rule_accounting_mismatch"):
        await run(fixture, provider)
    assert provider.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["BLOCKED", "UNSUPPORTED", "ERROR"])
@pytest.mark.parametrize("consequence", [None, "ESCALAR"])
async def test_unexecuted_rules_preserve_nullable_or_escalating_consequence(status, consequence):
    fixture = build_fixture("unsupported-rule")
    fixture.evaluation["rule_results"][0].update(status=status, applied_consequence=consequence)
    evaluation_id(fixture.evaluation)
    review = await run(fixture)
    assert review["status"] == "INCOMPLETE"
    assert review["original_preliminary_decision"] == "ESCALAR"
    assert review["rule_reviews"][0]["assessment"] == "UNCERTAIN"


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", ["not-a-date", "2026-09-19", "2026-09-19T12:00:00",
                                         "2026-02-30T12:00:00Z", "2026-09-19T12:00:00+25:00"])
async def test_review_timestamp_is_validated_without_optional_format_packages(timestamp):
    fixture = build_fixture("clean-approval")
    provider = FixtureProvider(fixture.response)
    with pytest.raises(ReviewInputError, match="invalid_reviewed_at"):
        await review_evaluation(fixture.evaluation, fixture.context, fixture.artifacts,
                                 provider, reviewed_at=timestamp)
    assert provider.calls == 0
