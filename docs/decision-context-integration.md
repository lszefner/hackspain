# Decision context integration guide

Status: implemented and verified offline against a disposable local Postgres and the local
filesystem backend. No live Supabase deployment has been performed; see "Deployment" below.

All commands in this document run **from the repository root**. Install extras once with
`uv sync --locked --extra worker --extra backend`. `rules_ingestion` and `backend` are
repository modules; they are not a published wheel API.

This document describes the implemented boundary between invoice extraction and business-rule
evaluation, and how to run, persist, and verify it. Where this document disagrees with older
design notes (including `docs/invoice-ruleset-erp-alignment-spec.md`), the machine schema
`rules_ingestion/decision_context.schema.json` and the code in
`rules_ingestion/decision_context.py` / `rules_ingestion/decision_storage.py` are authoritative.

## What the boundary is

Extraction output (schema `0.1`, `benchmark/schemas/invoice.json`) is immutable evidence.
Parsers continue to target that schema — not `DecisionContext` — and must preserve
annotations, issues, nulls, decimal strings, and evidence references.

A `DecisionContext` is a second, separate machine artifact that binds:

- the extraction evidence (invoice, reading, evidence, checks artifacts, by digest),
- the exact ruleset bytes evaluated,
- source snapshots for supplier/order master data, ERP payment status, and processed history,
- the explicit evaluation date,
- per-field facts with provenance (which source asserted the value, at what capture time),
- a preflight block describing data availability and execution support.

Evaluation (`evaluate_context`) produces a **recommendation** (`PAGAR`, `ESCALAR`,
`NO_PAGAR`) plus per-rule checks. It is never approval or payment execution, and there is no
payment-execution path anywhere in this layer.

## Machine contract

- Schema: `rules_ingestion/decision_context.schema.json`, JSON Schema draft 2020-12.
  `Draft202012Validator.check_schema` passes on it; `check_schema` is exercised in
  `tests/decision_context/test_decision_integration.py`.
- The schema adds, relative to the original design excerpts: root `file_id`, derived
  `invoice.line_amounts`, `Finding.source_ids`, and additional source kinds
  `master_workbook`, `source_mapping`, `extraction_auxiliary`.
- JSON Schema validation is necessary but not sufficient. `validate_context` additionally
  verifies artifact digests, source references, the exact internal extraction-source mapping
  (`invoice`, `reading`, `evidence`, `checks`), authoritative-scope consistency, and that the
  context can be replayed deterministically. Always run both.

Runnable Python usage (from the repository root, against the checked-in examples):

```python
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from rules_ingestion.decision_context import (
    SourceSnapshot, build_context, evaluate_context, load_schema,
    validate_context)
from rules_ingestion.decision_storage import create_decision_store

examples = Path("docs/examples/decision-context")
outcome = json.loads((examples / "outcome.json").read_text())
ruleset = (examples / "ruleset.json").read_bytes()
snapshots = {
    source_id: SourceSnapshot(
        **{**spec, "authoritative_for": tuple(spec["authoritative_for"])})
    for source_id, spec in
    json.loads((examples / "snapshots.json").read_text()).items()}

bundle = build_context(
    outcome, snapshots=snapshots, ruleset=ruleset,
    evaluation_date="2026-09-19", captured_at="2026-09-19T10:00:00Z")
validate_context(bundle.context, bundle.artifacts)
evaluation = evaluate_context(bundle)

schema = load_schema()
Draft202012Validator(
    schema, format_checker=FormatChecker()).validate(bundle.context)

store = create_decision_store("local", local_root=Path("/tmp/decision-context-demo"))
try:
    receipt = store.save(bundle)
    reloaded = store.load(receipt["context_id"])
finally:
    store.close()
```

`SourceSnapshot` fields: `kind`, `payload` (bytes or JSON-safe value), `captured_at`,
`asserted_by`, `authoritative_for` (tuple of scope strings), `scope` (human description),
`availability` (`available` | `partial` | `unavailable`), optional `as_of`.

`evaluate_context` never ingests raw text, prompts, or provider instructions; it only reads
the typed context fields and the frozen ruleset bytes bound into the context.

## Source snapshots

Snapshots are trusted **operator configuration inputs**, not invoice-controlled authority.
They are frozen into the context as digested artifacts.

- `workbook` (kind `master_workbook`): raw workbook bytes, single read, hashed at capture.
- `source_mapping` (kind `source_mapping`): original `sources.yaml` bytes.
- `suppliers` (kind `supplier_master`): payload `{records, provenance, workbook_sha256,
  config_sha256, missing_columns, sheet, warnings}`. `records` contains every tabular row,
  including blank-ID and malformed rows (kept for audit, not treated as identity); a record
  looks like `{id, razon_social, nif, iban, ciudad, condiciones}` per the configured column
  mapping. `provenance` is a parallel list of `{sheet, row, cells: {canonical: A1-address},
  raw: {canonical: raw_value}}` captured before normalization. Scopes:
  `supplier.identity`, `supplier.bank_details`, `supplier.payment_terms`. The current
  mapping does not declare an active-status column, so `supplier.active` is **not** claimed.
- `orders` (kind `order_master`): same shape; a record looks like `{pedido, proveedor_id,
  nif, importe_total, estado_excel, fecha_pedido}` per the configured mapping, with
  `currency` present only if a configured currency column supplies it. Scopes:
  `order.identity`, `order.amount`; `order.currency` is claimed only when a configured
  currency column actually exists (the current mapping has none, so it is absent).

See `docs/examples/decision-context/snapshots.json` for complete synthetic payloads.
- `erp` (kind `erp`): payload `{records: [{pedido, estado, page, row}], pages: [{page,
  xml}], complete: bool, error_code: str|null}`. `complete` is true only when pagination
  reached the declared final page with stable `total`/`paginas`/`por_pagina` metadata and the
  accumulated record count equals `total`. Any deviation (`page_mismatch`,
  `pagination_changed`, `count_mismatch`, `empty_page`, `malformed_record`, `invalid_xml`,
  `invalid_metadata`, `truncated_max_pages`, `connection_error`, `http_<status>`,
  `sensitive_response`, `invalid_response`) yields incomplete. Login responses are never
  captured; a page containing the token or password is rejected rather than stored.
- `processed` (kind `history`): payload `{kind: "processed", records: [{file_id,
  invoice_number, supplier_id, total, currency, issue_date, context_id?, captured_at?}],
  complete: false}` from the backend's latest-per-file review cache. Availability is always
  `partial` — this is not a complete ledger and is never used as paid/approved evidence.
  When the current file is evaluated, its own row is excluded (`exclude_file_id`).

Missing declared sheets produce `unavailable` snapshots; missing identity columns or
duplicate headers produce `partial`. Optional scopes that are declared but not configured
(e.g. `supplier.active`, `order.currency`) stay unbound, so dependent rule fields are
explicitly `unavailable` — distinct from `missing`, which is used only when an authorized
source exists but lacks a value.

## Running the offline CLI

Checked-in synthetic example inputs live in `docs/examples/decision-context/`:
`outcome.json`, `ruleset.json`, `snapshots.json` (generated from the test fixtures; the demo
VENDOR rule uses only supported parameters and is **not** the deployed balanced ruleset;
all supplier/order/ERP values are synthetic).

```bash
uv run --locked --extra worker --extra backend python -m rules_ingestion.decision_cli \
  --outcome docs/examples/decision-context/outcome.json \
  --ruleset docs/examples/decision-context/ruleset.json \
  --snapshots docs/examples/decision-context/snapshots.json \
  --evaluation-date 2026-09-19 --captured-at 2026-09-19T10:00:00Z \
  --backend local --local-root /tmp/decision-context-demo
```

Exit code `0` means the context was **stored and replay-verified**, even when the
recommendation is `ESCALAR` or the preflight is `blocked`. Read `decision` and `preflight`
in the JSON output — exit 0 is not approval. Exit `1` means a failure; the error line is
`error: <ExceptionType>: decision_context_failed; check configuration and source artifacts`
and never includes raw exception text (which could carry DSNs or tokens).

`--backend` is required. `payload_file` inside a snapshots entry is resolved relative to the
CLI working directory, for binary payloads such as the workbook.

## Persistence

Two backends; exactly one index per store.

- `local`: content-addressed blobs under `<local_root>/objects`, immutable
  receipts under `<local_root>/contexts/<context_id>.json` (exclusive create via
  temp-file + fsync + hard link; a conflicting existing receipt is an error, never
  overwritten). The revision path defaults to this backend (`REVISION_BACKEND` unset →
  `local`), but `create_decision_store` has no default and the CLI requires explicit
  `--backend`.
- `supabase`: artifacts go through `SupabaseStorage` (existing private bucket, default
  `invoice-ingestion-private`, overridable via `SUPABASE_STORAGE_BUCKET`) and
  `PostgresRepository` (`ingestion.artifacts` plus the new index table). If the Supabase
  backend is selected it **fails closed** — preflight verifies the private bucket and index
  readability before any paid work, and there is no fallback to local.

Save order: validate context → evaluate → upload and verify every source blob
(`decision-source-<kind>`) → upload schema bytes (`decision-context-schema`) → context
(`decision-context`, parented to all source + schema artifacts) → evaluation
(`decision-evaluation`, parented to the context artifact) → publish the index row **last**.
If any upload or verification fails, content-addressed bytes may remain unindexed; the next
save of the same bundle safely retries (idempotent). Orphaned bytes are acceptable and are
never deleted by this layer.

Load reverses it: receipt → digest-verified context/schema/evaluation blobs → JSON Schema +
`validate_context` → deterministic replay of the evaluation and byte-equality with the
stored evaluation. Loading a context written against a different schema version is an
explicit unsupported-contract error, not a silent parse.

### Index table

`supabase/migrations/20260919111816_decision_contexts.sql` creates
`ingestion.decision_contexts` on top of the existing `ingestion_alpha` migration:
`context_id` PK (`dc_<64 hex>`), `file_id`, the three artifact references, context/schema
SHA-256, `evaluation_date`, `data_status`, `execution_status`, `decision`, `created_at`.
Row-level security is enabled and `public`/`anon`/`authenticated` are revoked — the table
is backend-only; the Supabase secret key stays on the host and is never exposed to the
frontend.

"Immutable" here is an application-level property: the store only inserts (idempotent, with
column-by-column conflict comparison) and nothing in the code path updates or deletes rows.
A database owner/admin can still modify records — this is not cryptographic tamper-proofing;
integrity is enforced because `load` re-verifies every digest and replays the evaluation, so
drift is detected rather than silently trusted.

The backend's SQLite store (`backend/results_store.py`) is a **latest-per-file cache**, not
the record of truth. On a failed re-run it clears the current decision/checks but retains
the prior `decision_context` and `context_receipt` as stored evidence for API/CLI access;
the durable receipt/context bytes live in the decision store. No presentation changes ship
in this work.

## Environment

Externally supplied environment only — no `.env` file is read and no dotenv loading happens
anywhere in this path.

- `SUPABASE_URL`, `SUPABASE_SECRET_KEY`: required for the Supabase backend.
- `SUPABASE_DB_URL`: Postgres DSN for the index (`DATABASE_URL` is also honored by
  `PostgresRepository`; prefer `SUPABASE_DB_URL`). Requires a schema-owner or otherwise
  privileged server role, like the existing worker. Do not open the private schema to
  `anon`/`authenticated` to "fix" access.
- `SUPABASE_STORAGE_BUCKET`: optional bucket override.
- `REVISION_BACKEND`: `local` (default) or `supabase`; unknown values fail.
- `REVISION_EVALUATION_DATE`: required explicit `YYYY-MM-DD` business date for revision;
  the current date is never substituted.
- `REVISION_RULESET_PATH`: optional override for the schema-2.0 rules.json artifact; the
  default path may be absent and a missing/unparseable ruleset fails before any provider
  work.
- `REVISION_SOURCES_PATH`: optional override for `rules_ingestion/sources.yaml`.
- `HELMCODE_*`: only needed when intentionally running live extraction (`revisar_lote`);
  the decision CLI never calls providers.

## Deployment

Not performed. The migration builds on `ingestion_alpha`; apply it through the normal
migration workflow after verifying the target project and existing migration history.
Do **not** re-run `ingestion_alpha.sql` against a database that already has it (duplicate
constraints). To preview:

```bash
supabase db push --linked --dry-run
```

(verify flags with `supabase db push --help` on your CLI version; the above was checked
against the installed CLI). Then apply for real only after operator review of the target.

## Reproducibility and limits

- Reproducibility is pinned by: exact ruleset bytes and exact schema bytes (persisted and
  hash-verified), the registry/adapter code version identifiers, and the explicit evaluation
  date. Identical captured inputs produce the same content-derived `context_id`; new capture
  timestamps or changed inputs produce new IDs — that is expected. Version strings are
  identifiers, not archived executable code: replay uses the code currently on disk, so a
  `checks.py` change can alter an outcome and `load` will then reject the stored evaluation
  as drift. Loading a context written under a different schema digest fails explicitly.
- `DUPLICATES` bindings get `execution_status: "unsupported"` on the bound rule and the
  context `execution_status` becomes `"blocked"` pending a processed-vs-paid history policy.
- `require_active`, `check_nif_control_digit`, `check_iva`-style parameters are unsupported
  only when flagged true (a `require_active: false` does not block); any
  structured/pending/new rule condition kinds are unsupported and surface explicitly.
- All unreviewed annotations conservatively force review; credit notes are unsupported on
  the ordinary payment path; the current master data has no active flag and no order
  currency.
- Guarded supported legacy checks honor merged rule IDs, `on_fail`, and precedence, but this
  does not claim full policy correctness.
- ERP pagination completeness is a capture-level consistency check, not a freshness or
  transactional guarantee (`as_of` is unknown).
- No payment execution and no AI reviewer exist in this layer.

## Tests

```bash
uv run --locked --extra worker --extra backend pytest -q tests/decision_context
```

Postgres tests use only the disposable local instance from
`tests/decision_context/conftest.py` and skip when `initdb`/`postgres` binaries are
unavailable. No test calls paid APIs or shared databases.

## Compatibility note

`main` now includes the separate `alberto/` pipeline and a Next.js frontend;
`backend/server.py` is the preserved JSON API, not the frontend server. This work extends the preserved `ingestion`/`rules_ingestion` modules
plus the `backend/` revision path and the offline decision CLI; it does **not** wire this
context into `alberto/` and changes no current frontend. `frontend/`, `backend/server.py`,
and `alberto/` are preserved from `main` unchanged.
