# Deterministic invoice fast path

Measured on 2026-09-19 against live Supabase and the local ERP bridge. The
canonical `backend.run_revision.revisar_lote` path persisted extraction,
evaluation, output policy and run completion. No reviewer, rule-authoring,
vision or interpretation provider call was made during these measurements.

| Run | Wall time | ERP | Rules | Result |
| --- | ---: | --- | --- | --- |
| `speed-no-review-20260919-225424` | 10.237 s | Fresh capture | Imported from completed run | ESCALAR |
| `speed-no-review-20260919-225434` | **4.167 s** | Reused, under 30 s old | Reused | ESCALAR |

Both processed `2026-01-08_P001.pdf` through native text extraction. The invoice
still escalates because its currency is absent. These are two new durable
intents, not a `--status-key` replay. Rules were those already generated in
`e2e-1d-221545`; their exact sources and generation audit were preserved.

The warm measurement includes all writes through committed run completion.
Process/engine initialization and a preliminary read of the historical run
were outside the timer. The cold measurement includes first rules-cache
publication and fresh ERP capture. This demonstrates a warm invoice below
five seconds, **not** a cold-start or p95 latency guarantee. New unseen source
blobs, ERP refreshes, network variation and vision fallback can take longer.

## Current API read path

The backend now stores audit JSON in Postgres unconditionally; no storage-mode
environment variable is needed.
API GET requests use Postgres payloads only, including historical JSON already
stored there, with no Storage fallback or evaluator replay. Original/source
verification remains available through the core CLI. See
[Postgres-only API reads](postgres-api-reads.md) for query budgets and limits.
The timing measurements above predate this API read optimization.

## Configuration

The launcher exports configuration; application code never loads dotenv.
The local `.env` has these values, without changing credential values:

```dotenv
REVISION_REVIEW_ENABLED=false
REVISION_RULESET_RUN_KEY=e2e-1d-221545
```

- Review defaults to disabled when unset. `false`/`0` disables the provider and
  uses the deterministic evaluator's result directly, including `PAGAR`.
  `DISABLED` is a run policy, not a reviewer verdict. There is no payment action.
  Re-enabling review later cannot reinterpret past runs' output policies.
- Postgres JSON mode commits canonical JSON and its hash/metadata together in
  the existing artifacts table. Its addresses start with `postgres/`. Original
  PDFs, rule sources and decision source bytes remain in private Storage.
  Historical Storage-backed artifacts and compact artifacts remain readable
  through the core engine and ingestion export. No migration is required.
- The optional stored-run pin requires a completed run with a generation audit
  and matching workbook, mapping and profile. If those sources change, it
  refuses stale rules. Remove the pin to allow generation for new source bytes,
  or choose a newly approved run. `--ruleset`/`--rule-source` still work.
- Without a pin, durable rules reuse includes workbook/mapping/profile hashes,
  provider identity and generator implementation hashes. A durable generation
  claim prevents concurrent invoices or unknown outcomes from repeating paid
  authoring. Credentials are not part of the stored cache key.
- Only complete ERP snapshots are cached, for at most 30 seconds. Their original
  capture timestamp is retained. Failed/partial snapshots are not cached.
  Processed-invoice duplicate history is still queried separately per invoice;
  it does not inherit the ERP cache window.

## Where the time went

The previous path duplicated small JSON objects in Storage and Postgres,
performed repeated verification downloads, and used paid-provider job leases
for pure local computation. The changes remove that extra I/O:

- Request-scoped, bounded caches of verified immutable bytes and metadata.
  Exact shared sources reuse already verified object references. Original PDFs
  and parsed outcomes are freshly checked at the decision boundary. Forensic
  reads outside a request still verify persisted artifacts.
- Concurrent independent artifact operations and batched metadata inserts.
  Extraction trace queries are combined rather than fetched job by job.
- Native reading and deterministic interpretation publish their completed job
  and attempt traces atomically, without a pre-call paid-effect lease. Paid
  stages retain their lease/unknown-outcome machinery.
- Canonical audit JSON can stay in Postgres, removing upload/read-back pairs.
- Snapshot capture overlaps independent input persistence. Evaluations retain
  their order for duplicate history, while optional reviews can run concurrently
  without synchronous persistence blocking the event loop.

The final warm run made **2 Storage HTTP calls**, totaling 0.193 s, versus 54
in an earlier profiled intermediate run. It made 35 repository transactions;
some overlapped, so their summed durations are not wall time. The saved stage
timers also overlap. `revision_completed.latency_seconds` measures through
committed completion; the saved `before_summary_seconds` explicitly excludes
the final summary write and completion transaction.

## Verification

`tests/decision_context/test_revision_speed.py` covers durable disabled-review
policy, stored rules reuse across engine restarts, invalidation, failed
generation claims, review/evaluation overlap, scoped verification caches, ERP
expiry, stored-run pinning, compact review replay and corruption detection.
Its latency test uses disposable Postgres, mocked Storage with injected delays,
and a real local PDF. It never calls paid providers or shared databases and is
not a live latency or LLM-accuracy benchmark.

```bash
uv sync --locked --extra worker --extra backend
uv run --locked --extra worker --extra backend python -m pytest -q \
  tests/decision_context tests/test_core_engine_review.py \
  tests/test_contextual_review.py tests/test_contextual_provider.py \
  tests/test_deterministic_extraction.py tests/test_export_outcomes.py
```
