# Postgres-only API audit reads

The backend always stores canonical audit JSON in Postgres. No environment
variable selects the storage mode; old launcher settings have no effect.
Original PDFs and exact source bytes remain in private Storage.

## Read architecture

- GET requests and API startup require only Postgres. Storage clients and
  private-bucket checks are initialized lazily when launching ingestion.
- `backend/audit_reader.py` batch-loads record packets and their displayed JSON
  children. It verifies canonical hashes, sizes, content-addressed references,
  record IDs and record headers. It never contacts Storage or runs an evaluator.
- Historical Storage-addressed JSON is readable through its existing Postgres
  payload, with its original references unchanged. A missing or invalid payload
  is an explicit error, never a remote fallback. This does not reconstruct an
  audit that was never persisted.
- Invoice listing selects the latest input per filename before joining engine
  records, and transfers only review status, attention and error metadata.
- Flow traces select only the requested input and batch its jobs and attempts
  in one query, rather than fetching every job in the invoice batch and then
  querying attempts one job at a time.
- The summary endpoint queries invoice state once. Read caches are local to
  each request, so a subsequent read sees new runs and artifact changes.

The API projects saved decisions. Full original/source verification and
deterministic replay remain available through `InvoiceDecisionEngine.load`
and the core CLI `show` command. These have intentionally different costs.

## Before/after measurements

The opt-in `tests/decision_context/measure_api_reads.py` compares the API at
commit `99f2994` with this implementation on the **same persisted synthetic
invoice**, in disposable local Postgres. Extraction, review and Storage are
mocked. Neither side calls paid providers or a shared database. Both sides read
Postgres-mode JSON, isolating API-read improvements from the write-mode change.

Each database round trip receives 10 ms of simulated latency and each Storage
GET receives 20 ms. The table reports the median of three requests per endpoint
and mode. Responses are compared for equality (invoice detail compares its
public decision, evaluation, review, checks and invoice fields).

| Read | Review | Before | After | Speedup | DB queries before → after | Storage GETs before → after |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Summary | Disabled | 26.21 ms | 13.41 ms | 2.0× | 2 → 1 | 0 → 0 |
| Invoice detail | Disabled | 1168.87 ms | 40.00 ms | 29.2× | 48 → 3 | 18 → 0 |
| Full audit flow | Disabled | 1323.33 ms | 51.69 ms | 25.6× | 60 → 4 | 18 → 0 |
| Run status | Disabled | 63.80 ms | 23.79 ms | 2.7× | 5 → 2 | 0 → 0 |
| Summary | Enabled | 26.65 ms | 13.54 ms | 2.0× | 2 → 1 | 0 → 0 |
| Invoice detail | Enabled | 3110.97 ms | 40.45 ms | 76.9× | 127 → 3 | 46 → 0 |
| Full audit flow | Enabled | 3286.15 ms | 53.64 ms | 61.3× | 141 → 4 | 46 → 0 |
| Run status | Enabled | 63.47 ms | 25.01 ms | 2.5× | 5 → 2 | 0 → 0 |

These are controlled local measurements, **not production latency**, a
500-invoice load test, or a p95 guarantee. Actual latency depends on database
proximity, source and artifact sizes, and table history. The speedup comes from
projecting stored JSON instead of replaying evaluation and re-downloading all
source evidence on each GET. Full forensic verification remains a separate
core-engine operation.

The regular `test_api_postgres_reads.py` tests enforce query budgets for both
historical and compact addresses and both review policies. Storage access,
Storage initialization and evaluator execution are made fatal during reads.
Missing/corrupt JSON and API startup without Storage credentials are also
covered. API attempt latency strings and timestamp formatting are preserved
when batching the trace query.

Reproduce the comparison (requires baseline `99f2994` in local Git history):

```bash
uv run --locked --extra worker --extra backend python -m pytest -q -s \
  tests/decision_context/measure_api_reads.py --disable-warnings
```

The batching follows [Supabase's query optimization guidance](https://supabase.com/docs/guides/database/query-optimization).
No new database schema, live migration, backfill or invoice rerun is required
by this code change. Restart the API on this checkout to use it. Historical intents remain available via
status lookup; do not reuse a historical Storage-mode intent to launch new work.

```bash
uv sync --locked --extra worker --extra backend
uv run --locked --extra worker --extra backend python -m pytest -q \
  tests/decision_context tests/test_core_engine_review.py \
  tests/test_contextual_review.py tests/test_contextual_provider.py \
  tests/test_export_outcomes.py
```
