# Invoice ingestion agent: v-alpha implementation specification

Date: 2026-09-19
Status: alpha runtime implemented locally; live provider/Supabase trial and measured benchmark quality remain pending credential setup. See `docs/ingestion-agent-operator.md` for commands, validation, and implementation limits.
Scope owner: this conversation. Benchmark implementation is being handled separately.
Worktree: `/Users/liamszefner/orca/workspaces/500-sombras-de-alberto/invoice-ingestion`
Branch: `obs-liamszefner/invoice-ingestion`

## 1. Outcome and boundaries

Given a folder or explicit manifest of invoice PDFs, produce complete reading artifacts and evidence-linked invoice JSON, persist them in Supabase, and export reproducible results for the benchmark. Each input must have an explicit result, including failures. Support restarting interrupted work without discarding completed stages.

The alpha prioritizes working extraction and measurable correctness. Capture latency and cost from day one; optimize them after establishing the quality baseline. There is no user-requested spend cap, but retries and execution must remain bounded.

In scope: original preservation, page rendering, existing OCR integration, complete reading, Jev and DeepSeek interpretation, normalization, validation, provenance, resumability, and exports.

Out of scope: payment decisions, business-rule creation, ERP reconciliation, spreadsheet ingestion, relational invoice-domain tables, frontend, autonomous browsing, model training, custom OCR development, distributed orchestration, and production-readiness claims.

The term agent means a bounded extraction pipeline with model calls. Application code owns sequencing, persistence, retry policy, and validation. Models do not choose arbitrary tools or execute document instructions.

## 2. Evidence informing this plan

Seven PDFs were selected by filename-sorted positions 1, 2, 3, 250, 251, 499, 500 from a 500-PDF directory. Every page was rendered and visually inspected, alongside embedded text extraction.

| File | Observations affecting implementation |
| --- | --- |
| 2026-01-08_P001.pdf | One line; no explicit quantity or currency; invoice number includes a slash. |
| 2026-01-11_P007.pdf | Three description/amount lines; no explicit quantities. |
| 2026-01-12_P002.pdf | Repeated description with different amounts must remain separate lines. |
| FA-4666_transportes.pdf | Quantities expressed as x1; EUR symbol; supplier location and customer address. |
| FA-4673_informática.pdf | Spanish month-name date; quantities expressed as (1); alternative labels. |
| scan_028.pdf | Image-only noisy/tilted page; receipt stamp; explicit EUR; no text layer. |
| scan_029.pdf | Image-only noisy page; bank-account-change note; three line items. |

All seven are single-page. This sample establishes concrete cases, not complete corpus coverage. No due date, discount, withholding, installment, or product-code requirement was established by this sample. Preserve future occurrences through additional_fields before expanding the invoice contract.

## 3. Decisions and tradeoffs

| Decision | Why | Alternative and accepted cost |
| --- | --- | --- |
| Batch CLI, no UI | Fastest usable route from PDFs to inspectable outputs. | A review app could improve ergonomics; defer it. |
| Python package, uv lockfile, JSON Schema validation, Decimal arithmetic | Fits PDF tooling and the Python benchmark; enables exact amount handling. | Adds a small runtime beside the legacy ERP; do not modify ERP code. |
| Three independently persisted stages | Reading and interpretation can be debugged and rerun independently. | More artifacts and metadata than one model call, accepted for traceability. |
| JSON invoice output, not invoice-domain SQL tables | User chose schema flexibility for alpha; current samples are limited. | Direct relational reporting is deferred; JSONB remains queryable. |
| Supabase Storage plus Postgres | Agreed managed persistence for files, JSON, and job state. | Requires credentials and network; local exports are portable copies, not a competing state store. |
| Existing OCR engine | Do not rebuild recognition/layout technology. | Depend on provider capabilities; preserve native output and declare unsupported features. |
| First live OCR adapter: fal GOT-OCR2, provisionally | A documented hosted endpoint exists on compute the user already has access to. | String output may be insufficient for geometry/table fidelity; adapter integration is not a quality endorsement. |
| OSS fallback candidate: PaddleOCR document pipeline | Existing document-processing stack if the hosted output proves inadequate. | Self-hosting adds deployment work; do not implement both before the first trial. |
| Jev and DeepSeek behind one interpretation interface | Compare Choice-based assignment against generative extraction fairly. | Two narrow adapters; no automatic claim that either wins. |
| DeepSeek first vertical slice, Jev next | Producing arbitrary nested JSON is simpler for the initial end-to-end wiring. | This is implementation order, not a provider selection or rejection of Jev. |
| Fixed workflow, no autonomous agent framework | Finite stages and visible failures are enough for this task. | No speculative self-improving/tool-selecting loop. |
| Complete evidence plus selected interpretation | Preserve all data without forcing every fact into standard fields. | Unmapped content remains explicitly accessible, not automatically understood. |
| Nullable absent values, explicit uncertainty | Avoid plausible fabricated fields. | Consumers must handle incomplete records. |
| Independent benchmark ownership | Prevent evaluator drift and answer leakage. | Coordinate contracts through schemas; never edit references to fit predictions. |
| Versioned cache and bounded retries | Resume safely and avoid unnecessary paid work. | Exactly-once external billing cannot be guaranteed after an ambiguous timeout. |

User-agreed choices are scope, CLI simplicity, Supabase, three layers, JSON output, existing OCR, Jev/DeepSeek comparison, benchmark coverage, and correctness-first priority. Python, CLI command names, first-adapter order, retry defaults, and persistence details below are proposed implementation defaults. They do not require another product-design round before implementation, but must be documented if changed.

## 4. Artifacts and contracts

### Stage 1: original

Store original PDF bytes unchanged in a private bucket under a content-hash path. Maintain a separate input record for every manifest entry, including identical PDFs with different filenames. Content deduplication must never collapse per-file benchmark results.

Render every page at an initial 200 DPI for image-based OCR; preserve page number, dimensions, render settings, and source hash. A configurable 300 DPI rerender is a targeted trial for small text, not a promise of better reading. Do not overwrite originals or earlier render artifacts. PDF rotation is respected; any additional transform is recorded.

Extract embedded text as auxiliary evidence. The alpha baseline runs the chosen OCR on every page rather than using an unvalidated text-only shortcut that might miss stamps or image regions. Embedded text remains separate unless a later, explicitly versioned reconciliation method is evaluated.

### Stage 2: complete reading JSON

Use `benchmark/schemas/reading.json` as the current benchmark-facing contract. Snapshot its hash in run metadata because the benchmark is actively being built.

Required information: schema_version, file_id, capability flags, every page, ordered blocks, literal text, table rows/cells when available, uncertainty records, and meaningful non-text elements when supported. Reading concerns document structure, not supplier/customer/total assignment.

Preserve the provider's full native response independently. Use a layout sidecar keyed by reading block/cell IDs for page dimensions, bounding boxes, confidence, and source mappings. The current benchmark reading schema does not admit coordinate fields; do not discard coordinates or modify that schema unilaterally.

For fal's documented string-only result, preserve page output as literal text. Deterministically split paragraph/line boundaries only where observable; classify unknown blocks as other. Set unsupported capabilities false. Never manufacture word boxes, OCR confidence, or semantic tables. If parsing explicitly returned HTML/Markdown tables, record the adapter transformation/version and keep the original string.

Call image OCR per page initially so source page attribution is unambiguous. A missing/failed page makes the reading partial, not complete. Preserve successful pages for retry, but block normal stage 3 execution until all pages are accounted for. Empty output on a visibly nonblank page is a reading issue.

Stable block IDs are scoped to a reading artifact, not universal across providers. Geometry stays tied to the exact render coordinate system. Preserve every block; do not drop headers, boilerplate, notes, stamps, or repeated lines.

### Stage 3: interpreted invoice JSON

The accepted payload is `benchmark/schemas/invoice.json` version 0.1. Do not recreate a divergent schema in the ingestion package. Load an explicit schema path, validate it, and record its hash. Any future schema change must update producer and evaluator together.

Fields:

- schema_version, file_id, document_type (invoice/credit_note/other/unknown)
- invoice_number, issue_date, purchase_order_reference, currency
- supplier and customer: name, tax_id, location, address
- payment: iban
- lines: position, description, quantity, amount
- taxes: label, rate_percent, amount
- totals: taxable_base, total
- annotations: kind (note/stamp/footer/other), text
- additional_fields: label, raw_value, normalized_value
- issues: field JSON Pointer, kind (unreadable/ambiguous/conflicting), raw_text, candidates

All declared keys remain present; unavailable scalars are null and empty collections are []. Amounts/quantities are decimal strings. Dates normalize only when unambiguous. IBAN whitespace is removed. Preserve spelling/accents and identifiers, including leading zeros. Quantities are never defaulted to one. Currency requires printed evidence such as EUR or €. Do not infer payment status from a receipt stamp or correct printed arithmetic.

Store evidence separately: JSON Pointer -> page plus reading block/cell IDs and, where practical, exact character spans. Every non-null factual value requires a valid source link. Technical fields (schema_version, file_id, position) are application-generated; document_type needs document evidence. Valid references prove traceability, not semantic correctness.

Also keep a coverage ledger: each reading block is mapped, preserved_unmapped, or unresolved. Preserve raw content in all cases. A high ledger coverage score does not prove OCR completeness against the PDF.

The benchmark's current provenance.json describes reference authorship/human review. Do not mark predictions codex_visual or human_verified to satisfy it. Runtime evidence is a separate sidecar and exports through the benchmark's prediction contract.

## 5. Processing workflow

1. Preflight config, schema hashes, writable storage, provider access, and model availability without displaying keys.
2. Snapshot the explicit input manifest, file hashes, and configuration. Stable relative paths identify inputs; exact filenames remain file_id for this corpus. Reject duplicate file_id values in an export rather than overwrite them.
3. Register batch/input rows; upload/verify content-addressed originals.
4. Render and persist pages; capture auxiliary embedded text.
5. Claim missing reading jobs; submit OCR; persist request IDs immediately when returned.
6. Save native responses, canonical reading, and layout sidecars; validate page accounting and schemas.
7. Claim requested interpretation job(s) for that exact reading hash.
8. Produce invoice JSON plus evidence; normalize deterministic formats; validate and persist checks.
9. Mark completed or needs_review; export one result envelope per input and interpreter.

No external provider submission if durable job intent cannot be recorded. Do not overwrite previous attempts, results, or accepted run configurations.

## 6. Interpretation adapters

### DeepSeek

Input: canonical reading only, plus extraction instructions and the frozen invoice schema. Do not supply ERP data, filename-derived hints, benchmark answers, or original images. File ID is inserted by code after inference.

Request structured output when the chosen endpoint supports it; otherwise JSON mode plus strict local validation. Verify the actual endpoint/model capabilities. The user has Helmcode access: prefer its available DeepSeek endpoint if compatible; otherwise configure a direct supported endpoint. Never silently substitute a different model.

Request a bounded envelope containing invoice and evidence, then export the invoice payload alone for scoring. Include all document blocks within the supported context/output budget. Do not truncate long invoices. If a document exceeds limits, report needs_review/unsupported_size until a versioned chunk-and-merge strategy is implemented and evaluated.

At most one format-repair call may receive the original reading, original output, and validation errors. Record the first failure and repair usage. No repair may use reference answers. A schema-valid but semantically uncertain result is not endlessly reprompted.

### Jev

Input is the same canonical reading. Code creates candidate spans from blocks/cells with exact offsets and adjacent context. Candidate generation must not consult benchmark answers.

Pipeline:
1. Enumerate conservative candidates for identifiers, dates, amounts, party spans, line groups, taxes, and annotations from the reading.
2. Use Choice questions to select candidate IDs for scalar fields and assign meanings to blocks/columns/rows. Include not_found and ambiguous choices.
3. Copy selected source strings and normalize them deterministically.
4. Assemble ordered line/tax arrays, additional fields, annotations, evidence, and unresolved issues.

Do not treat Jev as an arbitrary JSON generator. Do not enumerate every combinatorial possible invoice. Respect provider choice/question limits; report candidate overflow rather than silently discard options. Question IDs are not assumed visible to the model: instructions must identify the field and context explicitly.

Keep original candidate sets, responses, probabilities, confidence, and assembly version. Independent choices can conflict: validate incompatible assignments and leave uncertain fields unresolved. Grouped/multiline candidates are a known quality risk. Preserve unmatched blocks in the reading layer and coverage ledger.

No arbitrary confidence threshold becomes a correctness guarantee. Thresholds, if introduced, are configuration tuned on development data only. Candidate recall and selection accuracy belong in benchmark analysis.

## 7. Runtime, storage, and recovery

One Python CLI process with bounded asynchronous concurrency is sufficient. Initial default: two concurrent remote requests per configured provider; configurable after observing limits. Use ordinary application modules and provider SDKs/HTTP clients. No Redis, Celery, Temporal, or autonomous orchestration dependency for alpha.

Minimal persistence responsibilities (operational records, not invoice-domain normalization):

| Record | Responsibility |
| --- | --- |
| batches | Manifest/config snapshots and hashes, timestamps, batch status. |
| inputs | Batch + relative input path, filename, original content hash/object key. |
| jobs | Stage, input artifact hash, provider/model/config/schema versions, unique work key, state, lease. |
| attempts | Attempt number, provider request ID, timings, errors, usage, raw artifact pointers. |
| artifacts | Content hash, kind, storage object key, parent artifacts, validated JSONB payload for canonical outputs. |
| input_results | Each input/interpreter's selected artifact, completion/review/failure status. |

Use UUID primary keys, timestamptz timestamps, foreign keys, and indexes for foreign keys and claim/status queries. Enforce unique work keys atomically. JSONB holds canonical reading/invoice/evidence metadata; large raw responses/images/PDFs stay in Storage. Exact artifact bytes live in Storage; compute hashes from a specified canonical serialization, not Postgres JSONB output order.

Keep internal tables in a private schema with access limited to the backend worker. Buckets are private. No public frontend or anon access is needed. If exposed schemas are used, enable RLS and explicit grants/policies. Use a configured backend credential only in the worker; redact authorization headers, signed URLs, and credentials from persisted request metadata/logs.

Work key includes source/reading hash, stage, provider, model, provider revision when available, render/adapter/prompt/assembly versions, schema hash, and relevant settings. Distinct versions produce new jobs. Refresh requests create a recorded new generation; no silent cache bypass. Provider aliases with unknown revisions are explicitly marked non-reproducible across time.

Job states: pending, running, retry_wait, succeeded, needs_review, failed, unknown. Claim with a short atomic DB transaction and expiring lease; never hold a transaction across network calls. Completed jobs are reused. Resume reconciles expired jobs and provider request IDs before resubmission.

Retry defaults: three total transport attempts; exponential backoff with jitter, honor Retry-After, configurable per-attempt timeout (initially 180 seconds). Retry transient connection errors, 429, and 5xx. Authentication/configuration failures stop that provider queue with an actionable error. Do not retry invalid inputs indefinitely.

Unknown outcome after submission: query a saved provider request ID if possible. If not recoverable, record unknown and require explicit retry selection; explain that a second billable call may occur. Do not promise exactly-once external execution.

Storage and Postgres are not one transaction. Upload immutable artifacts first, verify success/hash, then atomically publish metadata and job completion. A crash after upload leaves an orphan that a retry can reuse; a job must never report success pointing at an unverified/missing artifact. No automatic destructive cleanup in alpha.

## 8. Validation, status, and observability

Separate processing validity from factual accuracy:

- Schema/type/date/decimal validation at input and provider-output boundaries.
- Every source page accounted for; every referenced block/cell exists.
- Evidence present for interpreted non-null facts.
- Duplicate descriptions preserved; positions deterministic and sequential.
- Nonempty content with an all-null interpretation is needs_review.
- Ambiguous/conflicting/unreadable issues and unresolved candidate groups cause needs_review.
- Arithmetic discrepancies are warnings; preserve printed values. Apply reconciliation checks only when their operands/meaning are established, and never infer absent discounts or zero values.
- No status claims human verification or perfect completeness.

Per-file result status: completed, needs_review, failed. Batch status additionally distinguishes partial and running. Export errors explicitly, not as fake empty invoices.

Structured operational events include batch/input/job/attempt IDs, stage, provider/model, timing, page/token counts, cache hit, error code, and known cost. Raw invoice contents live in private artifacts rather than console logs. Cost is unknown when pricing/usage cannot establish it; never zero by default.

Persist prompts/configuration without secrets and provider outputs for debugging. Display concise CLI counts and the path/ID for failures. Provider comparisons are separate runs, not silent failover that contaminates the benchmark.

## 9. Proposed CLI and module boundaries

Commands below are implementation targets, not currently runnable:

```text
uv run invoice-agent ingest --input facturas --ocr fal-got-v2 --interpreter deepseek
uv run invoice-agent ingest --manifest <manifest.json> --ocr fal-got-v2 --interpreter jev
uv run invoice-agent status --batch <id>
uv run invoice-agent resume --batch <id>
uv run invoice-agent retry --batch <id> --stage reading --failed-only
uv run invoice-agent interpret --reading-run <id> --interpreter jev
uv run invoice-agent export --batch <id> --output <directory>
```

Export contains manifest/config, canonical stage2 and stage3 files, evidence/layout sidecars, and one JSONL outcome envelope per input/interpreter. Failed envelopes contain structured errors and null artifact references. Ordering follows the manifest. Benchmark adapters consume successful payloads and failure envelopes without losing the input denominator.

Suggested package: ingestion/{cli,contracts,pdf,storage,jobs,validation,export}. Provider modules: ingestion/providers/{fal_ocr,deepseek,jev}. Put candidate generation and normalization in explicit testable modules. No imports of benchmark references, gold answers, or scoring logic in ingestion inference paths. Schemas are loaded as data, not regenerated by runtime.

## 10. Benchmark integration and ownership

The benchmark agent owns benchmark/ and reference annotations. This implementation owns ingestion runtime, Supabase migrations/config, and this spec. Existing .gitignore changes and benchmark files are unrelated work to preserve.

Use the accepted invoice and reading schemas as current contracts; reconcile changes at integration time and record hashes. Do not edit benchmark files concurrently. Runtime prediction provenance is distinct from reference review provenance.

Three tracks: OCR vs verified reading; Jev/DeepSeek on identical verified reading; end-to-end on identical actual OCR. Runtime does not access held-out answers. Reference-derived verified reading is allowed only as the explicit isolated-interpretation input.

Codex-generated reference annotations are drafts until user review. Report provisional scores separately. The user-selected 50 files are the benchmark; a proposed 30/20 development/held-out split is not assumed finalized. Seven already inspected invoices are development examples, not unseen tests.

No numeric quality threshold has been agreed. Do not invent an accuracy pass mark. Report measured field/line/document results and errors, then make the model-selection decision from reviewed evidence.

## 11. Implementation sequence and acceptance

1. Contracts and scaffold: validate current benchmark schemas; establish CLI/package/lockfile; add env example containing names only. Acceptance: an offline fixture validates and exports without provider calls.
2. Persistence and originals: migrations, private bucket, batch/input registration, immutable upload, render/page manifest. Acceptance: identical content under two input names preserves two results; restart does not lose originals.
3. OCR trial: run fal GOT-OCR2 on the seven inspected development PDFs. Compare rendered originals to output, especially scan notes/stamps, line boundaries, and identifiers. Acceptance: every page saved with truthful capabilities and visible failure states. If inadequate, trial existing OSS/available Helmcode OCR before declaring a reader selected.
4. First vertical slice: DeepSeek reading-to-invoice plus evidence, normalization, validation, Supabase persistence, export. Acceptance: all seven produce schema-valid results or explicit diagnostic failures, with no fabricated missing quantities/currency.
5. Jev implementation: candidate builder, typed choices, assembly and provenance. Acceptance: same input/output contracts; candidate omissions and unresolved groups visible; no gold-data dependency.
6. Recovery tests: restart after submission, after artifact upload, and before DB completion; 429/5xx, invalid response, missing page, unknown outcome, duplicate invocation. Acceptance: no lost input; completed work reused; retries bounded.
7. Benchmark integration: execute separate tracks on the benchmark-selected split once available; provide exact commands, failure reports, versions, usage, and provisional/reviewed distinctions.
8. Full-corpus run only after the vertical slice is functioning: account for all 500 inputs with explicit outcomes. This is operational coverage, not evidence of 100% accuracy.

Meaningful tests cover decimal/date normalization, missing quantities/currency, repeated lines, notes/stamps, evidence-reference integrity, conflicting assignments, cache-key invalidation, atomic claim behavior, and recovery across storage/DB boundaries. Use deterministic provider fixtures for routine tests and a small live smoke run for integration; do not turn routine test runs into paid batch inference.

Definition of implemented alpha: runnable end-to-end path, both interpretation adapters, persisted artifacts and provenance, restart/retry behavior, benchmark-compatible exports, tests, and operator README. Definition of measured quality: separate reviewed benchmark results. These are different milestones.

## 12. Remaining verification, not reopened product decisions

- Obtain/configure Supabase, fal, Jev, and DeepSeek/Helmcode credentials securely during implementation. Access claims do not establish configured credentials.
- Verify Helmcode's actual account model catalogue and endpoint features; public site descriptions do not establish available OCR.
- Select the reader after the development trial, not vendor claims.
- Resolve unsupported layout/non-text capabilities honestly; preserve the original and page image even when the reader misses information.
- Confirm benchmark export contract and schema snapshots with the parallel benchmark work before integration.
- Decide any schema changes exposed by real documents explicitly, including unreadable descriptions or multiple IBANs that v0.1 cannot represent as first-class fields. Preserve all alternatives in evidence/additional fields/issues meanwhile.

## Sources and implementation-time verification

Public capability references checked earlier in this conversation; verify current API details before coding:

- fal GOT-OCR2 input/output: https://fal.ai/models/fal-ai/got-ocr/v2/api (documented outputs are strings).
- Helmcode managed inference: https://dev.helmcode.com/docs and https://helmcode.com/.
- PaddleOCR existing OSS pipeline: https://github.com/PaddlePaddle/PaddleOCR.
- Jev Choice: https://docs.typesafe.ai/primitives/choice (supplied candidates, not arbitrary string generation).
- DeepSeek: https://api-docs.deepseek.com/ (verify chosen endpoint/model structured-output support).
- Supabase Storage: https://supabase.com/docs/guides/storage.
- Supabase JSONB: https://supabase.com/docs/guides/database/json.
