# Next PR: benchmark the complete canonical engine

## Scope

Add a reproducible benchmark boundary for the canonical backend flow:

`frozen rule sources -> ruleset -> PDF extraction -> deterministic evaluation -> contextual review -> persisted run`

This document records follow-up work. It does not change benchmark fixtures, ground truth, thresholds, sampling, prompts, or scoring in the current PR. Database migrations and live-provider access remain separately managed by the project owner.

## What already exists, and what it proves

- `rules_ingestion/eval/score_pipeline.py` scores rule classification, structural extraction from spreadsheets, and ruleset merge invariants. Offline mode is lexical and skips cases declared to require an LLM. Its pass criteria come from the existing fixture manifest.
- `make eval-score` invokes that scorer with `--regen`. Do not use it casually during a release audit: regeneration changes fixture artifacts. Preserve the checked-in ground truth for comparisons.
- `ingestion/export.py` exports extraction outcomes, canonical reading/invoice/evidence artifacts, and benchmark-import metadata. It does not export the complete new engine evaluation/review record graph.
- `ingestion/benchmark_evidence.py` exports persisted OCR request evidence. Its current CLI loads dotenv; controlled runs must respect the repository's explicit-configuration/no-dotenv rule rather than invoke it blindly.
- `tests/decision_context`, `tests/test_rule_execution.py`, and the contextual-review tests verify contracts, persistence, determinism, and failure handling. Scripted model responses are not measured model accuracy.
- Legacy golden-data and ERP integration tests exercise the separate `alberto` path. Passing them does not by itself certify the new backend.
- The repository's `benchmark/` directory currently supplies extraction schemas, not a full-engine scoring runner.

Therefore, neither the existing lexical score nor a passing mocked-provider suite is an end-to-end accuracy result for the canonical engine.

## Freeze the benchmark contract first

Identify the target scorer and accepted import schema before writing an adapter. Do not guess compatibility with an external benchmark. Agree on the expected outcomes and scoring semantics separately from the implementation being evaluated.

The adapter must account for every selected input exactly once. Failed, unknown, blocked, unsupported, and incomplete cases must remain visible; do not silently filter difficult cases out of a metric's denominator.

Do not translate a preliminary `PAGAR`, a completed run, or a supportive AI review into payment authorization.

## Required reproducibility bundle

Specify and version an export containing:

- Run/request identity, batch/input IDs, source PDF hashes, and the immutable selected-input manifest.
- Exact ruleset bytes/hash and its source Excel/CSV/profile/mapping bytes, with provenance basis recorded. Caller-attested historical sources must not be presented as a verified original build.
- Frozen master/ERP/history snapshots, including scope, authority, availability, completeness, and capture time.
- Raw reading/provider artifacts, structured invoice, evidence links, extraction checks, and errors.
- Full deterministic context and evaluation, including rule traces, unsupported capabilities, decisive reasons, and completeness.
- Contextual review request/response/result, prompt/model identity, reviewed evidence, limitations, and any bounded-view projection metadata.
- Code/release identity, schema hashes, dependency/runtime identity, explicit evaluation date, and non-secret execution configuration.
- Per-stage usage, latency, retries, cache reuse, and recorded cost when known. Unknown cost must not become zero.

Full original source artifacts must remain retrievable even when the review request uses bounded evidence views. Artifact hashes must be verified across export/import boundaries. Use a new output location for different bytes rather than overwrite another run's evidence.

## Separate evaluation tracks

### 1. Extraction

Evaluate structured fields against labeled original PDFs/readings using the established normalization contract. Preserve distinctions between missing, ambiguous, uncertain, and wrong values. Include both text PDFs and scanned/annotated documents.

### 2. Deterministic rules

Compare both per-rule results and preliminary invoice decisions against independently authored expected outcomes for a pinned policy and frozen source state. Cover:

- Supplier identity, account matching, and configured NIF control-character checks.
- Amount/total reconciliation and configured VAT arithmetic, including explicit unsupported or ambiguous tax shapes.
- Authoritative paid-state and submitted/processed duplicate evidence, without conflating submission and payment.
- Dates, payment terms, required fields, and authorization limits.
- Missing authority, partial history, unsupported configuration, evidence corruption, and per-rule execution errors.

Report coverage/completeness alongside decision correctness. A safe escalation caused by an unimplemented required check is not evidence that the full policy was executed successfully.

### 3. Contextual review

Use independently labeled semantic concerns and citations to measure missed-detail detection, unsupported challenges, evidence validity, and review coverage. A syntactically valid citation does not establish that the model's business conclusion is correct.

Track `COMPLETED`, `INCOMPLETE`, and `FAILED` separately. Reviews using projected evidence remain explicitly incomplete; do not score them as exhaustive inspection of the archived snapshots. Evaluate supportive and challenged preliminary `PAGAR` cases as well as hard rejections.

### 4. Operational behavior

Test idempotency, interrupted runs, unknown provider outcomes, restart behavior, immutable history, and retrieval without local input files. Distinguish safe refusal to repeat an unknown call from automatic workflow recovery.

Measure whole-run and per-stage latency, provider calls/usage, known cost, artifact traffic, and behavior as history grows. In particular, the current processed-history path loads/replays prior evaluations; verify that invoice batches do not incur unacceptable repeated whole-history work. Do not claim throughput from a tiny mocked fixture.

Numeric release thresholds and dataset/sample selection must be explicitly approved and versioned before live scoring. Do not weaken existing thresholds to make a candidate pass.

## Offline versus live execution

### Offline default

- No paid providers and no shared database.
- Use frozen artifacts, deterministic fixtures, disposable Postgres, and mocked storage/network surfaces as appropriate.
- Preserve the current rule-authoring fixture seed and pass criteria.
- Keep any temporary path-only mapping to the immutable Caja snapshot explicit; do not mutate source fixtures or treat a missing live `caja/` directory as an accuracy failure.
- Report simulated provider responses as contract coverage, never LLM accuracy.

### Explicit live gate

- Owner-approved database target, migrations, credentials, provider/model configuration, selected inputs, and spending bounds.
- No hidden dotenv loading or accidental access to an existing production ERP/database during test collection.
- Preflight the intended private storage/database and provider configuration before submitting paid work.
- Start with a controlled smoke case proving complete persistence and retrieval, then execute the approved benchmark manifest.
- Preserve rate-limit/time-out/unknown states and use explicit intent keys. Never blindly retry potentially billed calls.

## Acceptance checklist for the next PR

- [ ] Full-engine export/import schema and target scorer are agreed and versioned.
- [ ] Every selected input appears once, including failures and unknown outcomes.
- [ ] Offline replay verifies original artifact bytes and uses the pinned evaluator release.
- [ ] Same inputs and implementation produce identical deterministic evaluation bytes; comparisons across implementations separate semantic changes from identity/hash changes.
- [ ] Expected outcomes cover the actual default policy, not only simplified test rules.
- [ ] Required authoritative fields and history coverage are available or explicitly recorded as blockers.
- [ ] Contextual-review quality is measured separately from deterministic decisions and extraction quality.
- [ ] Full-run source/rules/model/code identities and known costs accompany reports.
- [ ] A candidate cannot improve its score by hiding incomplete cases, regenerating expected answers, or changing thresholds.
- [ ] Live-provider and database smoke/benchmark gates have explicit owner approval and reproducible evidence.
