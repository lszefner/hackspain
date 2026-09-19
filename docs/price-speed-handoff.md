# Invoice ingestion: price and speed handoff

Snapshot: 19 September 2026. This document records the investigation performed on this date; it is not a completed performance benchmark. No optimization was implemented as part of this handoff.

## Goal and current gap

The product target is **under one cent per invoice and around 500 ms to process one invoice**. Confirm the target currency and whether 500 ms means median, p95, or an upper bound before setting acceptance criteria. This document treats it as individual invoice latency, not batch throughput. Provider prices below are USD, without currency conversion.

The current product route is **PDF → rendered page images → fal GOT-OCR2 → canonical reading → Jev Choice → validated invoice JSON**, with Supabase Storage and Postgres persistence throughout. A DeepSeek adapter also exists, but the live measurements below concern Jev.

The latest observed completed reading-to-interpretation spans have a **185-second median**, about **370 times 500 ms**. This excludes initial upload/render and final outcome persistence. The selected OCR service lists **$0.05 per image**, already above a one-US-cent budget for a single-page invoice before interpretation and infrastructure.

## Measured product results

Source: read-only queries against the configured runtime database during the investigation. Batch `1f5dd9b2-38af-4b07-b872-d4b78fdd24f2`, created `2026-09-19 00:50:52 UTC`. Its recorded configuration has concurrency 2; helper-worker logs exist, so that value does not establish total concurrent activity during the run.

| Measurement | Samples | Minimum | Median | Maximum | Mean |
|---|---:|---:|---:|---:|---:|
| Reading attempt latency | 21 with recorded latency | 9.17 s | 83.50 s | 180.21 s | 86.30 s |
| Interpretation attempt latency | 12 with recorded latency | 33.43 s | 48.12 s | 224.66 s | 97.91 s |
| Reading start to interpretation finish | 11 inputs with completed stages | 67.54 s | 185.08 s | 531.87 s | Not calculated |

Interpretation results marked `needs_review` count as completed stages for the combined span. Stage-latency samples include every joined attempt with non-null recorded latency; they are not a clean one-attempt-per-document cohort. The combined span uses the earliest attempt start and latest attempt finish among rows whose job is currently `succeeded` or `needs_review`, requiring both stages. Retries/recovery can therefore extend it. The stage medians use different populations and must not be added together.

At this snapshot the batch was still marked `running`. Persisted input outcomes were **4 completed, 7 needs_review, and 10 failed**. Other selected inputs had no final persisted outcome. This is a partial 50-input run, not a successful 50-invoice benchmark. Failure costs and unfinished latency are not represented by the completed-span median. A database `running` state alone does not prove a worker is still alive.

An earlier single-input smoke batch, `36322d5c-cf99-42bf-9d6d-0c1ba8fcd625`, recorded:

- Reading: **83.23 s**.
- Interpretation: **25.49 s**, resulting in `needs_review`.
- Reading start to interpretation finish: **115.39 s**.
- Batch creation to batch update at completion: approximately **122.64 s**. This includes batch overhead and is not an isolated inference measurement.

### What the timers cover

In `Pipeline.job`, the latency clock starts after job claiming and attempt creation. It covers the stage operation, including provider calls and their internal persistence, but is captured **before** saving the final stage artifact and completing the job. It also excludes time waiting for the stage semaphore.

Consequently, `latency_seconds` is neither pure model inference nor full invoice end-to-end latency. There is not yet a trustworthy completed-batch throughput figure or full end-to-end p95 in this evidence.

## Price

The [fal GOT-OCR2 model page](https://fal.ai/models/fal-ai/got-ocr/v2), checked on 19 September 2026, states **$0.05 per image**. The current code submits one rendered image per page.

| Component | Current knowledge |
|---|---|
| OCR | Published list-price estimate: $0.05 × submitted page images, before any applicable billing adjustments |
| Jev | Usage is collected, but a verified monetary conversion/account rate was not established |
| Storage/database | No per-invoice allocation measured |
| Failed, retried, or unknown submissions | Can incur cost; successful logical-call usage is not a complete billing ledger |
| Total invoice cost | **Unknown**; runtime `cost_usd` remains null |

Illustrative list-price arithmetic, **not billed totals**: one page costs $0.05 for OCR alone; two pages $0.10; 500 one-page invoices $25, before Jev and infrastructure. Reusing persisted completed calls can avoid new submissions, but cache hits must be reported separately from first-time processing.

A sub-cent claim needs account-verified rates, billable submissions including failures/retries, and a stated treatment of infrastructure. Do not convert unknown costs to zero.

## Bottlenecks and evidence

### 1. Every page goes through remote OCR

`Pipeline.read` renders all pages at 200 DPI by default and awaits OCR for every page. `pdf.py` already extracts embedded text, but the product does not use it to bypass OCR. Multi-page PDFs are processed sequentially within an invoice.

Four saved fal timelines reported provider inference durations of **3.635, 6.311, 25.570, and 43.134 seconds**. Their stage times were much longer; one stage took **96.81 s** with **3.635 s** reported inference. Queueing, network, persistence, polling, and worker scheduling occupy the remainder; this evidence cannot assign an exact percentage to each.

The saved timelines reconstruct status observations from content-addressed artifacts. Repeated identical statuses share artifact identity, and one result timestamp predates its attempt. **Do not use artifact creation timestamps as exact per-poll timestamps or calculate precise queue duration from them.** Provider-reported inference metrics remain distinct from these reconstructed observations.

The observed OCR inference alone exceeds the 500 ms target. Removing our overhead will not establish a subsecond scanned-invoice path on this evidence.

### 2. Synchronous persistence blocks the async worker

`Pipeline.artifact` synchronously writes Storage and database metadata. `SupabaseStorage.put` uploads each uncached object, then downloads it to verify bytes before publishing its metadata. A bounded in-memory verified-object cache skips this work for recently verified objects.

For each provider HTTP call, `request_callback` persists the request before sending it, persists the response afterward, and records artifact links. Successful Jev calls also persist a separate reusable response artifact. **OCR submit, status polling, and result retrieval all use this callback.** This creates many sequential remote operations around a single model operation.

These calls run directly inside async functions; they block the event loop rather than yielding while Storage/Postgres responds. Raising document concurrency cannot fully overlap them and can delay other provider calls, polls, and timeout handling.

Existing local profiling recorded five fresh database connection/query samples at **405–475 ms**, versus five reused-connection query/commit samples at **132–134 ms**. These are environment-specific historical microbenchmarks, not server SQL execution time. **The current repository already has connection pooling**, so “add a pool” is not an outstanding fix. Synchronous round trips and transaction frequency remain relevant.

### 3. Interpretation requires serial Jev calls

The current adapter, `jev-choice-0.2`, defaults to 12 questions per request. `_ask` awaits batches sequentially. It first selects scalar fields and classifies groups, then builds another dependent set of questions for row fields. Large candidate sets add sequential tournament requests; oversized request bodies can split into further sequential batches.

Every request repeats the reading, invoice schema, and instructions. This increases submitted material and multiplies request/persistence overhead. The exact dollar effect depends on Jev's account billing semantics and must be measured.

[TypeSafe's documentation](https://docs.typesafe.ai/introduction) says questions within one request are evaluated in parallel and independently. That supports testing larger request batches, subject to actual API/input limits and quality checks; it does not prove a particular speedup for this workload.

The saved product event logs contain no `provider_request_completed` events. The current code emits `network_seconds` and `persistence_seconds`, but the available logs do not yet support a reliable Jev network-versus-persistence breakdown. Also, measured async network elapsed time can include event-loop scheduling delay caused by other synchronous work.

### 4. Smaller cumulative costs

- `request_callback` constructs a fresh `httpx.AsyncClient` for each call, preventing connection reuse across those calls.
- fal polling defaults to a **0.5-second sleep** between unfinished polls, before request/persistence overhead.
- Original upload, PNG rendering, page artifacts, canonical readings, final stage artifacts, and outcomes add work beyond the provider-stage timers.
- Default document/stage concurrency is 2; it affects throughput, but increasing it alone cannot meet individual-invoice latency goals.

No current profile establishes rendering, JSON validation, or candidate construction as the dominant bottleneck. Measure them before optimizing them.

## Suggested improvement sequence

These are investigation proposals, not changes already made or promised speedups.

1. **Establish clean measurements.** Record per-input arrival/start/result timestamps, semaphore wait, render/text extraction, provider request count, queue observations, provider-reported inference, persistence, validation, and final commit. Report latency and throughput separately, with failures and cold/cache-hit runs separate.
2. **Reduce persistence overhead while retaining recovery guarantees.** Investigate async or safely offloaded I/O, fewer transactions, consolidated artifacts, and avoiding full artifact persistence for every unchanged queue status. Retain durable submission intent, provider request IDs, raw evidence, hash integrity, and unknown-outcome handling.
3. **Reuse provider HTTP connections and reduce serial Jev requests.** Test larger bounded batches and parallel independent batches/tournament groups. Preserve dependencies between role classification and row extraction; measure rate limits and accuracy.
4. **Evaluate a digital-PDF fast path.** Use embedded text/layout only when quality checks support it, retaining an OCR fallback. Determine corpus coverage first. Text availability alone does not prove complete extraction of the visible invoice.
5. **Evaluate a different scanned-document reading path if 500 ms remains mandatory.** Current OCR price and observed inference do not meet either target. Compare complete extraction quality and operating cost; do not select solely on advertised speed.
6. **Reconcile billing.** Verify provider rates and count all billable requests, including failures and unknown submissions. Report known spend and unknown exposure separately.

For each change, run the same development inputs with fixed references and report both quality and performance. Keep held-out inputs out of tuning. A throughput improvement is not evidence of 500 ms individual latency.

## Quality boundary

The historical benchmark `jev-dev-v3` reports 84.93% provisional present-value recall across 30 development invoices, 0% whole-document correctness, and no human-reviewed references. It uses a historical experimental adapter on the isolated interpretation track. **It does not measure the current product adapter or end-to-end OCR accuracy.** Its approximately 6.94-second average recorded interpretation latency is not comparable to the complete product path.

The product's `completed` status is a runtime result, not a human-verified correctness label. Performance improvements should preserve all document content, field-to-source evidence, explicit uncertainty, and recovery behavior.

## Code and evidence map

| Location | What to inspect |
|---|---|
| [`ingestion/pipeline.py`](../ingestion/pipeline.py) | `job`, `read`, `request_callback`, timing boundaries, synchronous artifact work |
| [`ingestion/storage.py`](../ingestion/storage.py) | `SupabaseStorage.put`, verified-object cache, `PostgresRepository._run`, existing connection pool |
| [`ingestion/providers/jev.py`](../ingestion/providers/jev.py) | `_ask`, request splitting, tournaments, dependent extraction phases |
| [`ingestion/providers/fal_ocr.py`](../ingestion/providers/fal_ocr.py) | Queue submission, polling, result retrieval |
| [`ingestion/pdf.py`](../ingestion/pdf.py) | Rendering and embedded-text extraction |
| [`ingestion/config.py`](../ingestion/config.py) | DPI, concurrency, timeout, Jev batch/call limits |
| [`ingestion/events.py`](../ingestion/events.py) | Available content-free runtime timing fields |
| [`docs/ingestion-agent-operator.md`](ingestion-agent-operator.md) | Setup, export, recovery, and product benchmarking instructions |
| [`benchmark/reports/jev-dev-v3.md`](../benchmark/reports/jev-dev-v3.md) | Historical isolated-adapter result; not the product baseline |

Local-only investigation evidence, which may disappear and is not required to read this handoff: `/tmp/jev-io-profile.jsonl`, `/tmp/jev-fal-timeline.jsonl`, `/tmp/jev-product-50-events.jsonl`, and `/tmp/jev-product-50-helper-events.jsonl`. Their relevant findings and limitations are summarized above; do not depend on these paths existing on another machine.

### Refreshing the snapshot

With the existing environment configured, `uv run invoice-agent status --batch 1f5dd9b2-38af-4b07-b872-d4b78fdd24f2` reads the batch state. Database stage measurements come from `ingestion.jobs` joined to `ingestion.attempts`; final outcomes come from `ingestion.input_results` joined to `ingestion.inputs`. Filter all queries by batch ID and use a read-only connection.

For a completed product batch, follow the operator guide's export and `benchmark.import_product` workflow. That scores actual product outputs offline. `benchmark run` executes historical experimental adapters and should not be substituted for a product performance measurement.

Do not resume/retry just to inspect status: those operations can initiate paid inference. This handoff itself did not submit new inference or change runtime behavior.
