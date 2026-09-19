# Task 2 → Task 3: evaluation-result/1

User-provided handoff, accepted 2026-09-19. This is the Task 2 interface assumed by
Task 3, not a claim that Task 2 has shipped. The contextual implementation and
operator instructions are in [contextual-double-pass.md](contextual-double-pass.md).

## Required inputs

1. The immutable `EvaluationResult` below.
2. Its exact referenced `DecisionContext`.
3. The frozen artifacts referenced by that context: invoice, reading/page-block
   text, evidence, ruleset, master, ERP, and available history.

Use synthetic Task 2 fixtures until integration. Do not depend on evaluator
internals or require a particular future context version.

```typescript
type Decision = "PAGAR" | "ESCALAR" | "NO_PAGAR";

type Json =
  | null | boolean | number | string
  | Json[]
  | { [key: string]: Json };

type Ref =
  | { field: string }
  | { source: string; pointer: string };

type RuleStatus =
  | "PASS"
  | "VIOLATED"
  | "NEEDS_REVIEW"
  | "NOT_APPLICABLE"
  | "BLOCKED"
  | "UNSUPPORTED"
  | "ERROR";

interface EvaluationResult {
  schema_version: "evaluation-result/1";
  evaluation_id: string;
  context_id: string;
  context_sha256: string;
  context_schema_version: string;
  evaluation_date: string;

  ruleset: {
    source: string;
    sha256: string;
    ruleset_version: string;
    policy_id: string;
  };

  evaluator: {
    version: string;
    capability_version: string;
    implementation_sha256: string;
  };

  policy: {
    precedence: ["NO_PAGAR", "ESCALAR", "PAGAR"];
    verdict_semantics: {
      PASS: "PAGAR";
      NEEDS_REVIEW: "ESCALAR";
      FAIL: "on_fail";
    };
  };

  rule_results: RuleResult[];

  completeness: {
    enabled_rule_ids: string[];
    accounted_rule_ids: string[];
    all_enabled_accounted: boolean;
    evaluation_complete: boolean;
    approval_eligible: boolean;
  };

  outstanding_findings: {
    code: string;
    severity: "info" | "blocking";
    rule_ids: string[];
    refs: Ref[];
  }[];

  preliminary_decision: Decision;
}

interface RuleResult {
  rule_id: string;
  rule_ref: { source: string; pointer: string };
  capability_id: string | null;
  status: RuleStatus;

  applicability:
    | "APPLICABLE"
    | "NOT_APPLICABLE"
    | "UNKNOWN"
    | "NOT_EVALUATED";

  compliance:
    | "PASS"
    | "FAIL"
    | "NEEDS_REVIEW"
    | "NOT_EVALUATED";

  complete: boolean;
  on_fail: "ESCALAR" | "NO_PAGAR" | null;
  applied_consequence: Decision | null;

  reason_codes: string[];
  explanation: string;

  inputs: {
    field: string;
    fact_pointer: string;
    state:
      | "present"
      | "missing"
      | "ambiguous"
      | "invalid"
      | "unavailable";
    value: Json;
  }[];

  evidence_refs: Ref[];

  trace: {
    node: string;
    operation: string;
    input_refs: Ref[];
    result: "TRUE" | "FALSE" | "UNKNOWN" | "NOT_EVALUATED";
  }[];
}
```

## Contract semantics

- One result per active rule, including blocked, unsupported, and error results.
  Preserve frozen ruleset order.
- `rule_ref` and trace node point into the frozen ruleset. `fact_pointer` points
  into the context; that fact provides transformations and recursive lineage.
- `{source, pointer}` identifies a context source and a JSON pointer into its
  frozen artifact.
- Money remains decimal strings, never floating-point values.
- `all_enabled_accounted` means no rule disappeared. It does not mean all rules
  were successfully evaluated.
- `approval_eligible` means the deterministic checks permit a preliminary
  `PAGAR`; it is not payment authorization.
- A justified `NO_PAGAR` survives unrelated blockers.
- **Confirmed database duplicates reject even if the previous record was only
  submitted/processed. Do not label them paid without payment evidence.** This
  explicit user decision supersedes earlier uncertainty in Task 1 documentation
  about whether processing alone can prohibit payment. It is not permission to
  change Task 1's legacy evaluator as part of Task 3.
- A new-account note is evidence, not authorization.
- `evaluation_id = "ev_" + SHA256(canonical result JSON excluding evaluation_id)`.
  Use the repository canonical-byte convention.
- Task 3 preserves the original result. Its separate review references both
  `evaluation_id` and `context_id`, and identifies challenged rule IDs/evidence.
  It must not rewrite the preliminary result or invent business authorization.

## Minimum synthetic fixtures

Clean approval; bank mismatch; database duplicate rejection; ERP already-paid
rejection plus unrelated blockers; unsupported rule; new-bank-account annotation.
The Task 3 fixtures additionally exercise submitted as well as processed duplicate
records. Their scripted model replies are contract tests, not measured LLM quality.
