# Invoice ingestion alpha

The worker preserves PDFs, renders every page, reads document regions through Helmcode vision (or optional fal GOT-OCR2), and interprets the canonical reading with either DeepSeek through Helmcode or Jev Choice. Supabase Postgres and a private Storage bucket hold runtime state; local exports are portable copies. The legacy ERP and benchmark references are not used by inference.

## Install and offline check

```sh
uv sync --locked
uv run pytest -q
uv run invoice-agent fixture --output /tmp/invoice-alpha-example
```

`fixture` uses a synthetic reading and invoice. It exercises contract validation, evidence, and export without network calls or a database. It does not measure OCR or model accuracy.

## Configure

Preserve existing `.env` entries. Add the empty names from `.env.example` and fill them locally:

- `SUPABASE_URL`: project HTTPS URL.
- `SUPABASE_SECRET_KEY`: modern `sb_secret_...` backend Storage credential, never a browser key.
- `SUPABASE_DB_URL`: backend Postgres connection string (including SSL options required by your project).
- `SUPABASE_STORAGE_BUCKET`: `invoice-ingestion-private` by default.
- `FAL_KEY`: required only for `--ocr fal-got-v2`.
- `HELMCODE_BASE_URL`: your account's OpenAI-compatible API base, including `/v1` where required.
- `HELMCODE_API_KEY`: Helmcode credential.
- `HELMCODE_DEEPSEEK_MODEL`: exact DeepSeek model ID available to your account. There is no direct DeepSeek fallback.
- `JEV_API_KEY` and `JEV_MODEL`: required only for the Jev interpreter.
- `HELMCODE_VISION_MODEL` and `HELMCODE_LAYOUT_MODEL`: optional, default `gemma4` and `qwen3.6`; must be accessible on the existing account.

Apply the SQL file in `supabase/migrations/` using your backend database connection or your project's normal Supabase migration workflow. The SQL creates a private `ingestion` schema and, on Supabase, a private `invoice-ingestion-private` bucket. Use a backend database role with access to that schema; no anonymous/client grants are added. If choosing a custom bucket name, create that private bucket yourself.

```sh
uv run invoice-agent preflight --interpreter deepseek
```

Configuration errors print variable names only. The worker snapshots schemas/configuration and verifies Storage writes before submitting inference. Provider aliases without a pinned revision are explicitly marked non-reproducible across time. A successful infrastructure preflight does not prove the quality of an extraction.

## First live run

Start with a folder containing the seven development PDFs listed in the alpha spec, or an explicit manifest:

```json
{"documents":[{"file_id":"2026-01-08_P001.pdf","path":"../facturas/2026-01-08_P001.pdf"}]}
```

Paths are relative to the manifest. An optional `sha256` is checked. Filenames must be unique within an export. Identical bytes under distinct filenames remain separate inputs.

```sh
uv run invoice-agent ingest --manifest docs/development-inputs.json --ocr helmcode-vision --interpreter deepseek --dpi 300
uv run invoice-agent status --batch BATCH_UUID
uv run invoice-agent resume --batch BATCH_UUID
uv run invoice-agent export --batch BATCH_UUID --output /tmp/ingestion-BATCH_UUID
uv run invoice-agent interpret --reading-run BATCH_UUID --interpreter jev
```

`interpret` creates a separate batch using the exact saved canonical readings. It does not read benchmark answers. `--schema-dir` is a global option before the command; the default is `benchmark/schemas`. Resume uses the accepted run's frozen schema snapshot.

## Failures and recovery

```sh
uv run invoice-agent retry --batch BATCH_UUID --stage reading --failed-only
uv run invoice-agent retry --batch BATCH_UUID --stage interpretation --include-unknown
```

Transient connection failures, HTTP 429, and 5xx receive bounded retries. Read/write timeouts after submission are unknown outcomes, not proof that no call happened. Saved fal request IDs permit result retrieval without a second submission. Irrecoverable unknown calls require `--include-unknown`, which explicitly accepts possible duplicate billing. Authentication errors stop that provider queue. Completed jobs are reused; attempts and raw artifacts are retained.

Keep output directories separate for different export snapshots. Exports refuse to replace differing files. `outcomes.jsonl` contains every manifest input with `completed`, `needs_review`, or `failed`, including structured errors and nullable artifact references. `stage2/` and `stage3/` contain benchmark-schema payloads; evidence, layout, checks, raw output, configuration, and schema snapshots remain separate. `records.jsonl` translates outcomes to the benchmark record vocabulary where the source hash is known. An unreadable source has no invented hash and remains in `outcomes.jsonl`.

Operational stderr events contain batch/input/job/attempt IDs, stage, timing, state, and error codes, without invoice contents or keys. Private attempt artifacts hold requests and raw responses. Cost is `null` when it cannot be established; no zero-cost claim is made.

## Alpha limits

Live credentials and provider model availability must be checked after configuration. Local fixtures and database tests do not establish live Supabase/fal/Helmcode/Jev behavior or measured extraction quality. Inspect the seven-PDF trial before the 500-PDF run. String-only OCR advertises no semantic layout/table capabilities; page images and native output remain available. Missing or empty OCR pages block interpretation. Oversized model inputs report an explicit diagnostic instead of truncating. Jev candidate overflow is explicit, and unsupported or conflicting candidate assignments require review.

The benchmark's independent evaluator and human reference review determine extraction quality. This worker never labels predictions human-verified.

## Benchmark handoff

Each export includes `benchmark-import/<file-hash>/metadata.json` plus the interpreter's `request.json` and `response.json` when available. Metadata uses the evaluator's reading serialization hash separately from the worker's canonical-byte artifact hash. Use the benchmark owner's `init-run --provider import`, `import-output`, and `record-failure` commands; create matching OCR/end-to-end runs so their reading hashes agree. Keep all failed outcomes in the selected manifest. This worker does not mutate benchmark runs or reference annotations.

## Implementation defaults and measured checks

- Invalid model JSON fails explicitly with its native response retained. There is no automatic paid format-repair call in this alpha.
- Jev selects scalar candidates and classifies every literal line/table row. Assembly supports trailing printed amounts and explicit `xN`/`(N)` quantities; complex multiline/column combinations remain a quality risk and require review. Candidate/question limits fail visibly.
- The local suite exercises real PDFium rendering, a disposable PostgreSQL database, mocked HTTP through the actual adapters, immutable storage verification, leases, bounded attempts, and recovery. PostgreSQL tests skip when `initdb`/`postgres` binaries are unavailable.
- Live two-invoice trials and explicit AI visual review have been performed; see `hard-two-repair-audit-20260919.md`. They do not establish human-reviewed accuracy or full-corpus reliability.

API contracts used: [fal queue](https://fal.ai/docs/documentation/model-apis/inference/queue), [fal GOT-OCR2](https://fal.ai/models/fal-ai/got-ocr/v2/api), [Jev Choice](https://docs.typesafe.ai/primitives/choice), and [Helmcode documentation](https://dev.helmcode.com/docs). Account catalogue verification is performed at preflight; actual inference support is checked by the first small live run.

## Product Jev extraction, version `jev-choice-0.2`

The product now selects line descriptions, quantities, amounts, tax labels/rates,
and additional-field labels/values through source-bound Jev Choice questions.
Candidates include literal substrings with exact block/cell offsets. Table cells
remain independent of column order; repeated rows retain their order and identity.
No regex assigns a trailing number to a row field. Missing quantities remain null.
Required strings fall back to preserved source text only with an explicit review issue.

Questions are batched (12 per request by default). Oversized option sets use groups
of at most 252 candidates plus three abstention options, then compare winners.
Every generated candidate is offered; an ambiguous group makes the field unresolved.
This tournament can still miss a correct value and must be evaluated as part of
product accuracy. Requests also enforce the configured input limit and a 256-call
budget; no input is silently truncated. The Jev stage has a 600-second deadline,
inside the worker's 900-second lease; each HTTP call retains its 180-second timeout.

Successful subcall responses are persisted as private, hash-checked artifacts.
Automatic retries and explicit retries of failed/unknown jobs reuse identical
completed requests, including across worker restarts. Unknown calls still require
explicit retry authorization because their server outcome can be billable.
A retry of a needs-review result starts fresh. Usage sums the successful logical
calls, including reused responses; it is not a complete billing ledger for unknown
or failed attempts. Raw requests/responses remain retained separately.

New batches record `jev_adapter_version`. Resume/retry refuses old Jev versions;
use `invoice-agent interpret --reading-run BATCH_UUID --interpreter jev` to create
a new versioned batch from completed readings. This does not change old results.

For product evaluation, export these actual pipeline outputs and use the existing
benchmark import/scoring path described above. The benchmark's own provider
adapters are historical/experimental extractors, not the product implementation.
The saved 84.93% development recall belongs to that historical adapter and draft
references. It does not measure `jev-choice-0.2`. Keep references fixed, include
failures, and use a development-only manifest while improving extraction; the
runtime currently does not filter the benchmark manifest's held-out entries.

Validation for this update uses controlled provider responses and disposable local
Postgres. It verifies extraction mechanics, provenance, batching, and recovery;
it does not establish live model accuracy or hosted Supabase operation.

## Score a full product batch offline

Use the actual product outputs, not `benchmark run` (which executes historical
experimental adapters). A full run over the frozen 50-file selection is:

```sh
uv run invoice-agent ingest --manifest benchmark/manifest.json --interpreter jev --concurrency 8
uv run invoice-agent export --batch BATCH_UUID --output /tmp/product-BATCH_UUID
uv run python -m ingestion.benchmark_evidence --batch BATCH_UUID --export /tmp/product-BATCH_UUID
benchmark/.venv/bin/python -m benchmark.import_product --export /tmp/product-BATCH_UUID --run-id product-jev-BASELINE
```

The last command makes no model calls. It imports the persisted OCR request
bodies, readings and invoice outputs, checks source/artifact hashes, and writes
`benchmark/reports/product-jev-BASELINE{,-ocr}.{json,md}`. Each report includes
all selected inputs and separate development/held-out aggregates. `needs_review`
outputs are still scored; failed outputs remain failures in the denominator.
Use a new run ID for a new product batch. Draft references mean provisional scores.
OCR and interpretation latencies are stage measurements, not total batch wall time;
unknown billing costs remain unknown. Use development results for subsequent tuning.

## Region vision and explicit reviews

The current vision reader detects foreground and mirrored background regions, preserves source crops and transforms, and keeps billed rows separate. Version 2 obtains an independent reading and an image-based comparison. The comparison must account for every first-pass block, including tiny notes and repeated rows. Raw requests/responses remain available; model agreement is not proof of correctness. This adds calls and latency. Configuration snapshots record `vision_verify` and `vision_reader_version`.

Background text is kept in separate annotations and is forbidden as evidence for foreground invoice fields. Unresolved characters and incomplete invoice essentials trigger review. OCR coverage measures block linkage, not source completeness or factual accuracy.

For an explicitly reviewed reading, use:

```sh
uv run invoice-agent interpret --reading-run BATCH_UUID --interpreter deepseek --reading-review REVIEW_JSON
```

The review file binds corrections to source and original-reading hashes, identifies the reviewer and inspected pages, and records a reason for every change. This creates a separate batch; it never overwrites the original automatic output. Review-assisted scores must be reported separately from automatic accuracy.
