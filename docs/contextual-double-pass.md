# Contextual double-pass review (Task 3)

## Status and scope

Implemented as repository-local Python modules and an offline synthetic CLI. Task 1's
`decision-context/1` foundation is available. Task 2's future `evaluation-result/1`
producer is represented by synthetic fixtures; its implementation is not imported.
The complete user handoff is preserved in
[evaluation-result-1-contract.md](evaluation-result-1-contract.md).

No UI, application server, `alberto/` integration, payment execution, database schema,
or existing extraction/rule-evaluation behavior is changed. No paid model calls or
shared-database calls were made for this feature. These modules, like Task 1's
`rules_ingestion` modules, are repository APIs, not part of the published ingestion wheel.

## Product decisions recorded from the discussion

1. **Two different jobs, not two competing payment decisions.** Task 2 executes
   deterministic rules. Task 3 reads the frozen context for details that execution
   missed and checks whether existing explanations are supported by their evidence.
   It reviews passing rules too; it is not told to find a particular rejection reason.
2. **Reasons are supplied.** Task 2 provides each rule's `reason_codes`, `explanation`,
   applicability, compliance, consequence, inputs, evidence, and trace, plus outstanding
   findings. Task 3 receives these without requiring evaluator implementation details.
3. **Confirmed database duplicates reject immediately: `NO_PAGAR`.** A previous
   submitted or processed record is sufficient under the user's explicit policy;
   payment evidence is not required to reject the duplicate. It *is* required to call
   that record paid. This supersedes the pending duplicate-policy note in Task 1's
   integration guide. Similar amount/date alone is not a confirmed duplicate.
4. **The original is immutable.** Task 3 cannot weaken a duplicate or already-paid
   rejection because an unrelated rule is blocked, or because the model fails. It
   records a separate review referencing the original evaluation and context.
   A duplicate discovered after a preliminary approval is a blocking finding for
   adjudication/re-evaluation; Task 3 does not silently rewrite the preliminary result.
5. **Notes are evidence, not authority.** A new bank-account annotation may challenge
   an otherwise passing account comparison. It does not authorize using the new account.
   Neither `PAGAR`, `approval_eligible`, nor a clean review authorizes payment.
6. **Backend first.** Deliver a callable module, explicit provider adapter, fixtures,
   and documentation. A later UI should show the original recommendation and separate
   review findings/citations/coverage, never relabel an AI review as human approval.

## Architecture and ADRs

```text
Frozen invoice/reading/evidence/master/ERP/history/ruleset
                       |
             Task 1 DecisionContext
                       |
          Task 2 deterministic evaluation
                       |
     immutable EvaluationResult + exact ContextBundle
                       |
    identity / artifact / reference / accounting checks
                       |
       one bounded contextual model call (Task 3)
                       |
       strict response and citation validation
                       |
         separate contextual-review/1 artifact
```

### ADR: LLM contextual review; provider-neutral boundary

A general LLM is the initial choice because this pass must connect observations across
invoice text, annotations, master records, history, and rules, explain discrepancies,
and cite evidence. TypeSafe/Jev's documented typed choices, scores, and probabilities
fit narrower predefined questions, such as whether a note mentions an account change.
No provider comparison or model-accuracy benchmark has been run; typed output and
confidence alone do not establish factual correctness.

`ReviewProvider` exposes `provider`, `model`, and `async review(request) -> ProviderReply`.
The concrete adapter is `DeepSeekReviewProvider`, using an explicitly configured
OpenAI-compatible HTTPS chat-completions endpoint and model. No default endpoint,
model, secret discovery, dotenv loading, retry, failover, tool calls, or agent loop.
There is no TypeSafe dependency. Swapping providers does not change the review contract.

The model is instructed to inspect source evidence before reconciling original results.
This is one contextual call, not a blind second vote and not two additional LLM calls.
Seeing the original result can still anchor a model; offline contract tests do not
measure that risk or establish that a model found every missed detail.

### ADR: context adapter instead of a future-version dependency

`DecisionContextV1Adapter` uses Task 1's public `validate_context` and its current
source/field layout. This validates its schema, lineage, source authority, and deterministic
context reconstruction. It does not call `evaluate_context` or the future Task 2 evaluator.
The old Task 1 preflight capability flags do not override a supplied Task 2 result;
this matters because Task 1 marked duplicate execution unsupported before the user's
explicit duplicate policy.

The core consumes `ContextView` via `ContextAdapter.inspect(context, artifacts)`.
Another context version can supply its own adapter with field-pointer mappings, source
metadata, identity, and ruleset binding. Unknown versions fail explicitly without an
adapter. An adapter is trusted application code, never chosen by document text; it must
validate that version's schema, authority, lineage, context ID, and source declarations.
The core independently checks artifact hashes and the evaluation's context digest.

### ADR: fail visibly; do not produce a replacement decision

Invalid input identities/contracts raise `ReviewInputError` before a provider call.
Known provider/response failures produce a hash-bound `FAILED` review with a safe error
code, no accepted partial findings, and `attention_required: true`. Cancellation propagates.
Custom adapters/providers are trusted code: unexpected programming exceptions outside the
specified boundary must be handled by the embedding service and never treated as approval.

Unavailable, partial, unreviewable, or unreviewed sources force `INCOMPLETE`. Every available
JSON artifact is supplied, including invoice annotations and page-block text; nothing is
silently truncated. Raw workbook/YAML artifacts are hash-checked but cannot be read by
this JSON-text reviewer, so their absence from the model payload is explicit. This can
make otherwise normal production contexts incomplete; add a separately validated reader
or adjudicate the limitation rather than pretending the binary was reviewed.

## Contracts and provenance

The JSON Schema dictionaries are exported from `rules_ingestion/contextual_contracts.py`:

- `EVALUATION_SCHEMA`: the Task 2 contract, with boundary validation of hashes,
  dates, required fields, enum values, and reference shapes.
- `RESPONSE_SCHEMA`: model-authored observations only.
- `REVIEW_SCHEMA`: the complete host-authored review envelope.

Runtime validation also checks frozen enabled-rule order, accounting, exact rule/trace
locations, context fact pointers and state/value equality, and source/evidence references.
Rules disabled with `enabled: false` are not active; omitted `enabled` retains Task 1's
active-by-default convention. Invalid/unsupported active rules cannot disappear.

Canonical bytes use `ingestion.contracts.canonical_bytes`: UTF-8, sorted object keys,
compact separators, non-ASCII preserved, nonfinite numbers rejected. Arrays retain order.
Money remains decimal strings; values are never recalculated or converted to floats.

| Identity | Content hashed |
|---|---|
| Task 1 `context_id` | `dc_` + SHA-256 of canonical context without `context_id` |
| `context_sha256` | SHA-256 of full canonical context, **including** `context_id` |
| Source `sha256` | Exact captured artifact bytes, not reserialized JSON |
| `evaluation_id` | `ev_` + SHA-256 of canonical result without `evaluation_id` |
| `review_id` | `rv_` + SHA-256 of canonical review without `review_id` |
| Reviewer `prompt_sha256` | Exact UTF-8 system prompt |
| Reviewer `request_sha256` | Canonical complete provider-neutral request |

Hashes detect mismatches; they are not signatures or proof of source truth. Callers must
retain the original result, context, and exact artifact bytes for replay/audit. Task 3
never updates `context.reviews` or applies the visual-reading corrections API. Separate
review IDs identify captured observations, not deterministic LLM behavior.

### Model response

```typescript
interface Citation { source: string; pointer: string; quote: string }
interface ContextualResponse {
  rule_reviews: {
    rule_id: string;
    assessment: "SUPPORTED" | "CHALLENGED" | "UNCERTAIN";
    explanation: string;
    evidence: Citation[];
  }[];
  findings: {
    code: string;
    kind: "MISSED_DETAIL" | "CONTRADICTORY_EVIDENCE" | "UNSUPPORTED_CONCLUSION"
        | "AUTHORIZATION_GAP" | "REVIEW_LIMITATION";
    severity: "info" | "blocking";
    rule_ids: string[];
    explanation: string;
    evidence: Citation[];
  }[];
  reviewed_sources: string[];
  limitations: string[];
}
```

Every original rule has exactly one review, in original order. `SUPPORTED` supports the
original result, including a rejection; it does not mean payable. Incomplete, blocked,
unsupported, errored, or needs-review deterministic checks must remain `UNCERTAIN`.
A challenge or uncertainty requires a corresponding blocking finding naming the rule.
Findings without an applicable existing rule use an empty `rule_ids` list.

Citations must resolve against the supplied, digest-checked JSON source. A quote is an
exact nonempty excerpt of the pointed-to string, or the full canonical JSON representation
of the pointed-to non-string value. Referenced sources must be in the reported reviewed
sources. Unknown rule IDs, unknown pointers, fabricated quotes, and model-authored
payment-decision/authorization fields fail validation. The reserved
`CONFIRMED_DATABASE_DUPLICATE` code and every `AUTHORIZATION_GAP` must be blocking;
provider attempts to downgrade them to informational fail validation. `REVIEW_LIMITATION`
findings may omit evidence when explicitly describing unavailable information and always
make the review incomplete, even if a model labels them informational or omits the
separate limitations list. Task 1 binary-root references are retained as input provenance,
but the model cannot cite unreviewable binary contents as if it read them.

**Citation validation proves the quote exists, not that the conclusion follows from it.**
Coverage is the model's validated self-report, not proof of attention or reasoning.
Prompt-injection instructions tell the model to treat source text as data, but tests of
the response boundary do not prove a live model is immune to injection. Human adjudication
and model-quality evaluation remain necessary before operational reliance.

### Host review envelope

The separate review contains `schema_version`, `review_id`, `evaluation_id`, `context_id`,
`context_sha256`, `context_schema_version`, an explicit timezone-bearing `reviewed_at`,
`original_preliminary_decision`, and constant `payment_authorized: false`.

It also includes the validated `rule_reviews`, `findings`, and `limitations`, plus:

- `status`: `COMPLETED`, `INCOMPLETE`, or `FAILED`. Completed means the bounded review
  completed its declared coverage, not that payment is approved. A completed review may
  contain a blocking challenge. A false `evaluation_complete` is never inferred true just
  because each individual rule happens to be complete.
- `attention_required`: true for incomplete/failed reviews, original non-`PAGAR`,
  non-eligible evaluation, original or new blockers, and challenged/uncertain rules.
- `coverage`: `reviewed_rule_ids`, `unreviewed_rule_ids`, `reviewed_sources`,
  `unreviewed_sources`, `unavailable_sources`, `unreviewable_sources`. An uncertain rule
  may be accounted for as reviewed without being resolved.
- `reviewer`: provider, requested/resolved model, prompt version/hash, request hash,
  request ID when available, and allowlisted nonnegative integer token usage.
- `error`: null or a safe `{code}`; no raw provider exceptions/bodies or secrets.

## Resource limits and operational signals

Default `ReviewLimits`: 90-second wall deadline, 200,000-byte complete canonical input
budget, 100,000-byte model payload budget. Oversize input makes no provider call. The HTTP
adapter additionally caps the entire response envelope at 100,000 decoded bytes and uses
an explicit timeout, no redirects, JSON-object mode, temperature 0, and 8,192 output tokens.
A non-`stop` finish reason fails. Duplicate JSON keys, NaN, infinity, malformed JSON, and
truncated output are rejected. These are conservative initial engineering limits, not
measured optimums or calibrated quality thresholds. No chunking silently reduces coverage.

Operator questions are: Which evaluation was reviewed? Did it fail or require attention?
Which artifacts/rules were not covered? Which provider/model/prompt produced it?
The persisted envelope answers these. `contextual_review_finished` uses Python logging
with allowlisted structured record fields (`event`, review/evaluation/context IDs, status,
error code). The embedding service chooses a JSON formatter/sink; this module does not
configure global logging. It does not log invoices, bank details, prompt bodies, or keys.
Token usage is not a price estimate. There is no metrics backend or deployment in this slice.

## Run offline

From the repository root:

```bash
uv sync --locked --extra worker --extra backend
uv run --locked --extra worker --extra backend python -m rules_ingestion.contextual_review_cli \
  --fixture database-duplicate --output /tmp/contextual-duplicate-review.json
```

The output parent must already exist. Output creation is exclusive: an existing file is
never overwritten. The file contains `synthetic: true`, the fixture name, original evaluation,
context, decoded synthetic JSON artifacts keyed by artifact reference, and separate review.
Fixtures use canonical JSON artifact bytes, so `canonical_bytes` reconstructs those blobs;
this export convention must not be used to reserialize arbitrary production artifacts.
Exit 0 means a non-failed review was exported; it can still be incomplete or blocking and
never means payment authorization. Standard output gives identities and status, not facts.

| Fixture | Original | Review | Important assertion |
|---|---|---|---|
| `clean-approval` | `PAGAR` | completed, no additional concerns | still not authorization |
| `bank-mismatch` | `ESCALAR` | completed, blocking finding | cite both bank accounts |
| `database-duplicate` | `NO_PAGAR` | completed, blocking finding | submitted/processed, not called paid |
| `erp-paid-with-blockers` | `NO_PAGAR` | incomplete | paid rejection survives unsupported rule |
| `unsupported-rule` | `ESCALAR` | incomplete | LLM cannot invent a deterministic capability |
| `new-bank-account` | `PAGAR` | completed, blocking challenge | missed note remains evidence, not permission |

The last fixture deliberately supplies a synthetic first pass that missed an annotation.
It does not claim Task 1's legacy evaluator would approve that context. All contexts are
built and validated by Task 1; all Task 2 results and model replies are explicitly scripted.
No fixture is a benchmark score, real invoice decision, or measured detection result.

## Python API and eventual integration

Offline example:

```python
from rules_ingestion.contextual_fixtures import REVIEWED_AT, FixtureProvider, build_fixture
from rules_ingestion.contextual_review import review_evaluation

fixture = build_fixture("new-bank-account")
review = await review_evaluation(
    fixture.evaluation, fixture.context, fixture.artifacts,
    FixtureProvider(fixture.response), reviewed_at=REVIEWED_AT,
)
```

For a deliberately authorized live call, use the same API with explicit configuration:

```python
from rules_ingestion.contextual_provider import DeepSeekReviewProvider

provider = DeepSeekReviewProvider(
    endpoint=configured_https_chat_completions_endpoint,
    model=configured_model,
    api_key=host_supplied_api_key,
)
review = await review_evaluation(
    immutable_task2_result, exact_context_bundle.context, exact_context_bundle.artifacts,
    provider, reviewed_at=explicit_review_timestamp,
)
```

This live example has not been executed. Do not copy a Task 1 legacy `{decision, checks}`
result into this API or relabel it `evaluation-result/1`. Integration must supply the real
contract, not reconstruct it from internal evaluator details.

Once Task 2 lands: pass its original result and exact context artifacts into this function;
persist the returned review alongside, not over, the original evaluation; expose both to
operators; adjudicate challenges through an explicit human/policy workflow or produce a new
context/evaluation. Never use `attention_required: false` as a payment-execution gate.
Embedding services must bound concurrency, manage retry authorization/billing, persist
attempts/reviews, and define their own deployment/UI workflow. Those are not silently added here.

## Verification

```bash
uv run --locked --extra worker --extra backend python -m pytest -q \
  tests/test_contextual_review.py tests/test_contextual_provider.py
uv run --locked --extra worker --extra backend ruff check \
  ingestion/contextual_prompt.py rules_ingestion/contextual*.py \
  tests/test_contextual_review.py tests/test_contextual_provider.py
```

Tests use synthetic Task 1 contexts, scripted provider replies, and `httpx.MockTransport`.
They exercise identities, immutability, ordering, provenance, malformed responses,
coverage, duplicate policy, failure preservation, explicit configuration, and offline CLI
non-overwrite behavior. They make no paid API or shared-database calls. Live model recall,
false-positive rate, semantic entailment, provider availability, and operational latency
remain unmeasured.
