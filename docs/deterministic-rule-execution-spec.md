# Deterministic rule execution: implementation specification

Date: 2026-09-19

Status: implemented and verified offline. This specifies the shipped task-2 code, not the earlier design proposal. The result schema and runtime validators are authoritative. This is an opt-in Python/CLI path; the existing backend, frontend, persistence, and `alberto/` pipeline are not switched over.

## 1. Scope and ownership

The engine evaluates frozen, aligned invoice evidence against predefined business rules and produces an explainable preliminary `PAGAR`, `ESCALAR`, or `NO_PAGAR`. It does not execute payments or grant business authorization.

- Task 1 owns extraction evidence, source authority, normalized facts, identity resolution, lineage, and the existing v1 context/storage path.
- Task 2 owns validated executable semantics, per-rule accounting, applicability/compliance/consequence separation, deterministic aggregation, and the immutable result interface.
- Task 3 owns contextual agentic review. It can consume the result and supporting artifacts after `ESCALAR` or `NO_PAGAR`; its reviewer, prompts, routing, and review persistence are not implemented here.

Rules are defined before execution and run automatically. No runtime human approval gate or rule-approval-record workflow was added. Every active rule is accounted for, including unsupported and errored rules, even after an earlier rule establishes rejection. Disabled discovery drafts are not silently activated.

Non-goals: live source acquisition inside checks, PDF/raw-text interpretation as business policy, arbitrary generated Python execution, database migrations, new persistence, UI integration, payment destination selection, FX conversion, a universal tax engine, and benchmark/provider execution.

## 2. Change inventory

| File | Responsibility |
| --- | --- |
| `rules_ingestion/conditions.py` | Narrow typed condition validation/interpreter, three-valued truth, currency dimensions, and guarded execution views of facts |
| `rules_ingestion/execution_rules.py` | Canonical capability/parameter validation, phase-aware bindings, all six canonical dispatches, and per-assertion traces |
| `rules_ingestion/execution_context.py` | Explicit v1-to-v2 context preparation, reconstructive validation, evidence guards, and history observations |
| `rules_ingestion/evaluator.py` | Immutable result, rule error isolation, aggregation, decisive reasons, evidence-link enrichment, implementation identity, and result validation |
| `rules_ingestion/evaluation_result.schema.json` | JSON Schema draft 2020-12 for `evaluation-result/1` |
| `rules_ingestion/evaluation_cli.py` | Offline input loading and JSON packet export without storage writes |
| `rules_ingestion/evaluation_examples.py` | Six runnable synthetic task-3 packets based on existing checked-in task-1 fixtures |
| `tests/test_rule_execution.py` | Execution, provenance, policy, contract, CLI, and regression acceptance tests |
| `docs/deterministic-rule-execution-spec.md` | This implemented specification |
| `docs/decision-context-integration.md` | Cross-link distinguishing this opt-in path from legacy integration |

The original v1 decision context, registry, checks, and v1 storage behavior remain unchanged. The canonical backend calls this evaluator through `rules_ingestion.engine`; frontend integration remains a separate follow-up. Existing registries supply aliases, canonical function names, parameter allowlists, and dependencies. The execution adapter adds evidence-linked tax-rate facts without changing v1 field meanings. No dependency manifest or lockfile change is required.

## 3. Public API and versions

| Component | Version |
| --- | --- |
| Aligned parent context | `decision-context/1` |
| Execution context | `decision-context/2` |
| Execution adapter | `decision-adapter/3` |
| Field registry | `decision-fields/1` (unchanged meanings) |
| Capabilities | `decision-capabilities/3` |
| Structured conditions | `condition/1` |
| Evaluator | `rule-evaluator/2` |
| Result | `evaluation-result/1` |
| Frozen ruleset container | `2.0` |

```python
from rules_ingestion.execution_context import (
    prepare_context, validate_execution_context,
)
from rules_ingestion.evaluator import evaluate, validate_evaluation
from rules_ingestion.evaluation_cli import evaluation_packet

execution_bundle = prepare_context(task1_bundle)
validate_execution_context(execution_bundle)
result = evaluate(execution_bundle)
validate_evaluation(result.to_dict(), execution_bundle)
packet = evaluation_packet(execution_bundle, include_artifacts=True)
```

`task1_bundle` is the existing `ContextBundle` returned by `decision_context.build_context`. It already contains the exact ruleset source bytes. There is no independent ruleset argument to `evaluate` that could disagree with the context.

`EvaluationResult` is a frozen dataclass holding canonical JSON bytes. `to_json()` returns those immutable bytes; `to_dict()` returns a fresh decoded value. Mutating a returned dictionary cannot change the result. Context bundles themselves still contain nested dictionaries: preparation/evaluation copy their inputs and integrity validation detects changes; the dataclass alone is not deep immutability.

`evaluate` requires an explicitly prepared v2 bundle. It does not silently reinterpret a v1 context's unsupported bindings. `execution_context.load_schema()` derives the v2 schema in memory from the unchanged v1 schema; there is no separately maintained v2 schema file.

## 4. Frozen context preparation and evidence safeguards

Preparation validates the original v1 bundle, preserves its fields, sources, findings, reviews, and evaluation date, and adds:

- `alignment_context_id`: the original v1 content identity.
- `capability_version`: the supported execution contract.
- `input_guards`: field-scoped unusable-evidence findings propagated through field lineage.
- `invoice.taxes.<index>.rate_percent` facts from the explicit frozen invoice fields, with evidence/uncertainty handling. Rates are not inferred from labels. These execution-only fields are removed when reconstructing the v1 parent, then regenerated and integrity-checked.
- `history_observations`: separate hard/soft matches and coverage for processed, approved, and paid history.
- Rebuilt bindings with dependency `phase` (`applicability`, `compliance`, `unconditional`) and `required` flags.
- Recomputed preflight and a new content-derived context ID.

V2 validation reconstructs the v1 parent, invokes task 1's original validator, regenerates the v2 additions, and compares the complete result. Removing blockers or forging history observations is not accepted merely because a caller recomputes a hash.

Evidence guards reuse the existing extraction validator to check the relevant reading schema, evidence references, and foreground-evidence restriction. Bad links propagate through the affected fact's lineage. Execution sees guarded fields as uncertain without changing the original fact value, state, transformation, or source artifact.

This is deliberately scoped: an invalid invoice-bank link cannot justify a bank-mismatch rejection, but it does not erase an independently valid ERP already-paid proof. Invalid supplier identity evidence prevents associating a database match with the invoice. Malformed evidence links yield accountable blocked results rather than aborting evidence enrichment.

Existing context findings remain in force. An annotation remains unreviewed evidence, not permission to change the bank account or pay. No review record, payment destination, or inferred missing fact is manufactured.

## 5. Rule dispatch and validation

The frozen ruleset uses merged `id`, not legacy `rule_id`. Missing or duplicate IDs fail ruleset validation. Rules explicitly disabled with boolean `false` do not enter the active inventory. Missing `enabled` retains the existing active default; malformed non-boolean values are accounted for as unsupported.

Canonical dispatch requires the recognized canonical category and its exact allowlisted `rules_ingestion.checks` module/function declaration. New implementations read typed facts directly; embedded/generated `source` strings are never executed. Unknown conditions, parameters, operators, fields, invalid flags, and incompatible operands are explicit unsupported results.

`on_fail` must be `ESCALAR` or `NO_PAGAR`. It maps a proved `FAIL` to its policy consequence; a missing input does not automatically invoke it. Duplicate rules intended to reject must be configured with `on_fail: NO_PAGAR`, as in the supplied duplicate examples and existing catalog policy.

Boolean parameters must be real booleans. Monetary parameters must be finite and nonnegative. Decimal strings and integer parameters are supported; numeric JSON tokens from legacy frozen artifacts are parsed directly as `Decimal` before effective parameters are serialized as strings. This preserves the frozen token's precision instead of calculating through a binary float. Defaults and effective canonical parameters are exposed in traces.

Explicit `input_fields` are unconditional requirements. Other dependencies come from canonical behavior, enabled flags, parameter-selected fields, and typed condition operands. A rule with no executable assertions blocks rather than passing vacuously.

## 6. Canonical capabilities

| Canonical | Implemented behavior and defaults |
| --- | --- |
| `VENDOR` | Require resolved supplier; compare invoice/master tax IDs and IBANs by default. `require_active` defaults false; when true, require an authoritative usable boolean and fail if inactive. `check_nif_control_digit: true` validates Spanish DNI/NIE/CIF control characters, including an optional ES prefix and K/L/M special NIFs. It does not verify registration or replace supplier matching. Invalid format/checksum follows `on_fail`; unusable evidence blocks. Legacy advisory normalization behavior and policy flags remain unchanged. |
| `DUPLICATES` | ERP-pending check defaults true. Hard history key is invoice number plus resolved supplier ID. Soft key is amount/date with mandatory matching currency. Only those key meanings and `soft_duplicate_verdict: NEEDS_REVIEW` are supported. |
| `AMOUNT` | Line sum versus taxable base; base plus VAT versus total; and invoice versus same-supplier order amount default on. Absolute difference equal to the default EUR `0.01` tolerance passes. Optional nonempty currency allowlist. Monetary arithmetic requires EUR; no implicit conversion. Empty line lists do not prove reconciliation. `check_iva: true` verifies one explicit VAT row as taxable base × stated rate / 100, rounded half-up to cents and compared using the configured inclusive tolerance. It requires usable base/rate/amount/EUR evidence, accepts rates 0 through 100 without assuming a legal rate, and fails an out-of-range rate. Multiple tax rows or missing rate/base allocation block; rates are never guessed from labels. Non-VAT/unknown total reconciliation remains unsupported. |
| `AUTHORIZATION` | Usable EUR total strictly above `escalate_above_eur` (default `10000`) requires review. Equality passes. Missing currency is not EUR. |
| `DATES` | Future dates fail by default (`allow_future: false`). Payment-term enforcement defaults true: elapsed days greater than authoritative supplier terms require review; equality passes. Uses the frozen evaluation date and records elapsed days. |
| `MISSING` | Defaults to the existing registry's NIF, IBAN, order, amount, VAT, and date aliases. Required-field lists must be nonempty. Known missing values fail presence requirements; unavailable, invalid, ambiguous, unlinked, or uncertain evidence blocks. Present zero and false are not missing. |

Compound canonical checks continue collecting assertions. A known failure can coexist with unfinished assertions: the rule remains `VIOLATED`, with `complete: false` and retained blockers. It must not be converted into a successful or fully evaluated rule.

Arithmetic uses local decimal contexts rather than inheriting process precision. The decimal parser accepts fixed-point decimal strings up to 200 characters. Reconciliation runs with precision 500; v1 reconstruction uses the existing default precision 28 explicitly for stable compatibility.

## 7. Duplicate and history semantics

A hard match compares exact nonempty invoice-number and resolved supplier-ID strings. It does not require matching amounts or proof that the earlier submission was paid. Reasons distinguish:

- Processed/submitted record: `DUPLICATE_SUBMISSION`.
- Approved record: `DUPLICATE_APPROVED_RECORD`.
- Paid record: `ALREADY_PAID`.
- Authoritative ERP `PAGADA`: `ERP_ALREADY_PAID`.

Each hard match returns `FAIL`, mapping through the configured duplicate `on_fail`. Matching amount/date/currency without a hard match returns `NEEDS_REVIEW`. Hard and soft matches are recorded separately; a nonempty task-1 combined match list is not blindly treated as payment proof.

A partial history snapshot can establish a positive hard match when its source kind, history event kind, authority, matching keys, and invoice identity evidence are usable. It cannot establish absence. The original task-1 history facts and global coverage findings remain unchanged; v2 adds separate observations instead of turning an unavailable legacy fact into a present one.

Negative duplicate clearance requires complete usable processed history and completeness for any supplied approved/paid history. Approved/paid sources absent from the bundle are optional. Malformed records make coverage incomplete. The current file is excluded from processed-history matching so re-evaluation does not duplicate itself; genuine paid evidence is not excluded merely because it names the same file.

ERP proof requires usable order identity, correct supplier ownership, and authoritative payment status. A `PENDIENTE` order's existence is not a duplicate. Partial/conflicting/unavailable ERP states remain unusable; the evaluator never mines partial raw ERP rows to bypass task-1 blockers.

## 8. Declarative conditions

Example condition for an already-defined rule requiring review above EUR 5,000:

```json
{
  "kind": "structured",
  "schema_version": "condition/1",
  "applies_when": null,
  "logic": "AND",
  "clauses": [
    {
      "field": "invoice.total",
      "op": "<=",
      "value": {
        "literal": {"type": "money", "amount": "5000.00", "currency": "EUR"}
      }
    }
  ],
  "then": "PASS",
  "else": "NEEDS_REVIEW"
}
```

Rules for the language:

- A nonempty flat `AND` or `OR` group, at most 100 clauses. Optional `applies_when` is another flat group. No recursive arbitrary expressions.
- Operators: `==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `exists`, `missing`.
- RHS is explicitly `{"field": "supplier.iban"}` or a tagged literal. Strings that look like references are not interpreted as references.
- Literal types: `string`, `boolean`, `decimal`, `integer`, `date`, `money`, `string_list`. Money uses `amount` and `currency`; other literals use `value`.
- Membership accepts a string and a nonempty string-list literal. Ordered comparisons require date or numeric/money types. Operand types must agree.
- Monetary fields add their invoice/order currency dependencies. Field-to-field and field-to-literal comparisons require matching explicit currencies; no FX conversion occurs.
- `exists` and `missing` omit the RHS. Known present/missing states give definite answers; other states or unusable evidence give `UNKNOWN`.
- `then` is exactly `PASS`; `else` is explicitly `FAIL` or `NEEDS_REVIEW`. `UNKNOWN` never selects the else branch.
- Structured-rule `params` must be empty; literal values are in the condition. Unversioned historical generated conditions, empty clauses, unknown properties, and unsupported semantics are rejected rather than guessed or silently repaired.

Three-valued truth: `AND` is false if any clause is false, true if all are true, otherwise unknown. `OR` is true if any clause is true, false if all are false, otherwise unknown. All clauses are traced, while decisive branches identify the fields required for the proof. A nonessential unknown branch need not prevent a determinate structured result; global context blockers and unconditional declarations still apply.

Applicability false yields `NOT_APPLICABLE` without requiring compliance-only facts; unknown applicability blocks. Unusable unconditional `input_fields` can still block an otherwise non-applicable rule. Unknown fields in a definition are unsupported semantics, not an escape to non-applicability.

Natural-language translation/approval is outside execution. Authors must define the exact condition before freezing it. This version neither calls a compiler nor equates category classification/model confidence with correct business meaning. It does not introduce a human approval workflow.

## 9. Result and consequence contract

The full shape is in [evaluation_result.schema.json](../rules_ingestion/evaluation_result.schema.json).

| Status | Compliance | Contribution |
| --- | --- | --- |
| `PASS` | `PASS` | `PAGAR` |
| `VIOLATED` | `FAIL` | Rule `on_fail` |
| `NEEDS_REVIEW` | `NEEDS_REVIEW` | `ESCALAR` |
| `NOT_APPLICABLE` | `NOT_EVALUATED` | None |
| `BLOCKED` | `NOT_EVALUATED` | `ESCALAR` |
| `UNSUPPORTED` | `NOT_EVALUATED` | `ESCALAR` |
| `ERROR` | `NOT_EVALUATED` | `ESCALAR` |

Final precedence is `NO_PAGAR > ESCALAR > PAGAR`. A review blocker prevents approval but does not erase an independently justified rejection. No active rules, or only non-applicable rules, produce `ESCALAR` with explicit reasons. Unexpected check exceptions become `ERROR` without raw exception text, and later rules still run. Invalid context integrity, unsupported top-level policy semantics, or broken ruleset identity are invocation errors rather than trusted recommendations.

Result groups:

- Identity: schema/evaluation/context IDs, full context hash, context schema version, evaluation date.
- Ruleset/policy: exact ruleset source/hash/version, policy ID, precedence, verdict mapping.
- Evaluator: evaluator/capability versions and implementation digest.
- `rule_results`: rule identity/reference, capability, status, applicability, compliance, completeness, configured/applied consequence, codes/explanation, input snapshots, evidence references, assertion traces.
- `completeness`: active/represented IDs, accounting completeness, evaluation completeness, and deterministic approval eligibility. Empty rule sets are not evaluation-complete. Evaluation completeness does not clear independent context findings.
- `outstanding_findings`: original context findings plus unresolved execution/data blockers.
- `preliminary_decision`: recommendation only.
- `decision_reasons`: direct causes of the winning recommendation. The engine always emits this additive field; the JSON Schema keeps it optional for earlier contract consumers. Runtime result validation requires the emitted reasons to match aggregation.

For `NO_PAGAR`, decisive reasons come from proved failure assertions, not unrelated missing-input assertions. Other unresolved issues remain visible in findings and rule traces. `approval_eligible` means the deterministic layer permits preliminary approval; it is not authority to pay or an agentic-review result.

Input records carry field IDs, context fact pointers, values, and states. Source references have `{source, pointer}`; field references have `{field}`. Trace nodes point to the frozen rule or clause. Optional trace metadata includes operands, computed values, tolerance, effective parameters, required-branch flags, verdicts, reason codes, and explanations.

## 10. Traceability, validation, and replay

The trace chain is decision -> rule -> assertion/calculation -> normalized input -> task-1 lineage/transformation -> source artifact -> evidence/page/block/row/cell reference where available. Result enrichment links invoice JSON pointers to the evidence artifact and matching reading elements. It does not reinterpret page text as policy.

IDs and hashes use `ingestion.contracts.canonical_bytes` and SHA-256. The evaluation ID is `ev_` plus the digest of the canonical result excluding `evaluation_id`; `context_sha256` hashes the full canonical context including its ID. Source hashes identify their original captured bytes, not reserialized JSON documents.

The implementation digest covers the explicit semantic source/schema allowlist in `evaluator.IMPLEMENTATION_FILES`, Python version, and actual `jsonschema`, `referencing`, `jsonschema-specifications`, `attrs`, and `rpds-py` versions. The evaluator checks that files/dependencies have not drifted since import. It does not use the current time or a random ID. Documentation, example presentation, and CLI serialization are not part of that semantic allowlist.

`validate_evaluation` checks schema, identity, result digest, exact active-rule accounting/order, policy consequences, input values/states and pointers, evidence/trace references, preserved findings, aggregation, and decisive reasons. It does not independently re-execute every business predicate or cryptographically authenticate an untrusted author. A matching digest is not a signature or proof of source truth.

Retain the original evaluator release/runtime for historical re-execution. Hashing code detects drift; it does not archive executable code. Existing v1 persistence/replay stays unchanged. `rules_ingestion.engine` persists v2 contexts/results separately from the v1 store. Capability/adapter/evaluator upgrades create new identities; old evaluations must not be silently reinterpreted or relabeled as results of the new implementation.

## 11. Task-3 handoff packet

`evaluation_packet(bundle, include_artifacts=True)` returns:

- `evaluation_result`: immutable evaluation represented as a fresh JSON dictionary.
- `decision_context`: a copied v2 context.
- `source_documents`: decoded frozen JSON artifacts keyed by context source ID.
- `artifact_bytes_base64`: original JSON artifact bytes keyed by artifact reference, for checking the source hashes without relying on JSON reserialization.
- `opaque_artifacts`: references to binary sources, not their binary contents.

Without `include_artifacts`, only the result and context are emitted. A packet with opaque artifacts is not a complete standalone binary replay archive; obtain those exact bytes through the existing artifact boundary when full context reconstruction is needed.

Task 3 should inspect `preliminary_decision` and use `decision_reasons`, the complete per-rule trace, applicable rule definitions, and relevant source/evidence context. Select relevant supplier/order/history records and text when constructing reviewer input rather than blindly forwarding full snapshots. Source text, including a claimed new bank account, remains untrusted evidence.

Review routing after `ESCALAR`/`NO_PAGAR` belongs to the integration/task-3 layer. Preserve the original result and create a separate review referencing both `evaluation_id` and `context_id`. A reviewer may challenge a conclusion but cannot silently rewrite the result, mutate invoice facts, or mint business authorization. No task-3 implementation is shipped by this change.

## 12. Offline operation

Run from the repository root. Use the existing locked dependencies:

```bash
uv sync --locked --extra worker --extra backend
```

Evaluate the existing synthetic task-1 input files, without providers or persistence:

```bash
uv run --no-sync python -m rules_ingestion.evaluation_cli \
  --outcome docs/examples/decision-context/outcome.json \
  --ruleset docs/examples/decision-context/ruleset.json \
  --snapshots docs/examples/decision-context/snapshots.json \
  --evaluation-date 2026-09-19 \
  --captured-at 2026-09-19T10:00:00Z \
  --include-artifacts
```

Both dates are explicit. Snapshot entries use the existing `SourceSnapshot` fields; optional `payload_file` paths are resolved from the CLI working directory. Output is JSON on stdout. Exit 0 means evaluation completed, including rejection/review; it does not mean payment approval. Input/configuration failures return exit 1 with a sanitized type/category message. The CLI writes no result database or artifact store and reads no `.env`.

Runnable task-3 examples:

```bash
uv run --no-sync python -m rules_ingestion.evaluation_examples --case already-paid-blocked
```

| Case | Expected recommendation | Purpose |
| --- | --- | --- |
| `clean` | `PAGAR` | Supported synthetic vendor rule |
| `bank-mismatch` | `ESCALAR` | Both accounts and policy-correct mismatch explanation |
| `database-duplicate` | `NO_PAGAR` | Positive processed duplicate despite partial history |
| `already-paid-blocked` | `NO_PAGAR` | ERP payment proof survives a blocked vendor check |
| `unsupported` | `ESCALAR` | Enabled empty structured condition remains accountable |
| `bank-note` | `ESCALAR` | Note does not authorize a new destination |

Example packets are labeled `synthetic: true` and use a synthetic policy ID. They are not a certification of the full balanced profile. The example generator imports no paid provider or rule compiler.

## 13. Verification and acceptance evidence

Observed before documentation: 140 focused tests passed, 2 selected local-persistence tests passed, and Ruff passed. The only observed warning was pytest-asyncio's Python 3.14 deprecation warning. No shared database, provider, benchmark, or server was used.

```bash
uv run --no-sync python -m pytest -q \
  tests/test_rule_execution.py \
  tests/decision_context/test_decision_context.py \
  tests/decision_context/test_decision_integration.py

uv run --no-sync python -m pytest -q \
  tests/decision_context/test_decision_storage.py \
  -k 'local_save_load_roundtrip or save_is_idempotent_and_dates_create_new_context'

uv run --no-sync ruff check \
  rules_ingestion/conditions.py rules_ingestion/execution_rules.py \
  rules_ingestion/execution_context.py rules_ingestion/evaluator.py \
  rules_ingestion/evaluation_cli.py rules_ingestion/evaluation_examples.py \
  tests/test_rule_execution.py
```

Acceptance coverage includes clean approval; bank `on_fail` variants; processed/approved/paid hard matches; partial positive matches; self-exclusion; soft-match review; ERP pending/paid/missing/partial/conflicting states; authority gaps and malformed history; active flags; unsupported parameters; exact threshold/tolerance boundaries; money/currency compatibility; applicability and decisive three-valued branches; required zero/false versus missing; date/term boundaries; no active rules; disabled drafts; inert embedded Python; per-rule error isolation; annotations; immutable results; replay and code drift; forged inputs/references/accounting/aggregation; valid original-byte hashes in six task-3 packets; and invalid/malformed evidence regressions.

## 14. Remaining limits and integration work

- Legal VAT-rate eligibility, multi-row VAT base allocation, mixed withholding reconciliation, arbitrary duplicate keys, regex/dynamic rule code, and unregistered business facts remain outside the implemented capabilities. NIF checksum validation is not a registry lookup; synthetic Caja identifiers can legitimately fail it.
- The current master mapping does not supply authoritative supplier-active status or order currency. Requiring those values blocks; the evaluator does not disable rules or invent defaults to create approval.
- Source coverage and authority are frozen assertions from trusted integration configuration, not guarantees of freshness or truth. No additional freshness policy is invented here.
- Current annotations conservatively require review. Human transcription verification and business authorization remain distinct.
- The canonical backend now routes through the persisted engine and contextual review. Frontend compatibility and full-engine benchmark work are tracked in `frontend-engine-contract-next-pr.md` and `full-engine-benchmark-next-pr.md`. Payment execution is not implemented, and live database migration/provider validation remains a separate owner-managed gate.
