# Live desk integration

Branch: `feat/live-backend-frontend`. The main Next.js desk now queries the
canonical backend instead of importing exported demo records. Open
`http://localhost:3000/?view=invoices`.

## Data and semantics

- The list contains persisted ingestion inputs only, latest per filename. PDFs
  merely present on disk are not counted. Reprocessing selects the newest input
  before filtering, so an older success cannot hide a new failure.
- Default filter: processed (has an evaluation, without a recorded extraction,
  run or review failure). Extracted, processing, and errors remain available.
  A processed record is not an approval or evidence of payment.
- Supplier groups are separated by supplier identity and currency. Unknown
  suppliers are kept separate by input identity. Missing amounts and currencies
  stay explicit. Unknown currencies are never aggregated into a money total.
- Recommendation follows `export_outcomes.decide_output`. The equivalent list
  SQL is tested against the canonical policy, including challenged/failed/
  incomplete reviews and explicitly disabled review. The detail response calls
  the canonical Python policy itself.
- Detail preserves original rule statuses and references, including BLOCKED,
  UNSUPPORTED and NOT_APPLICABLE. Non-passed checks appear first; passed checks
  are collapsed. Review findings remain separate from deterministic evaluation.
- Lifecycle shows received, extracted, evaluated, reviewed, derived output,
  resolution and payment. Missing timestamps are not invented. Resolution and
  payment are not recorded. Review DISABLED is explicit.
- Original PDF and full `/flujo` trace are fetched only on request. The UI never
  launches processing, authorizes payment, or sends messages during a read.

## Query interface

`backend/ui_queries.py` reads existing Postgres artifact payloads in one SQL
statement per request. It does not download audit packets from Storage, run
providers, create tables, migrate or backfill the database. Canonical records
remain immutable. Each request sees current committed data: no application
cache with stale invalidation is needed for the measured latency target.

| Backend GET | Purpose |
| --- | --- |
| `/api/ui/invoices` | Compact invoice page; search and filter in Postgres |
| `/api/ui/suppliers` | Supplier/currency groups and recommendation totals |
| `/api/ui/summary` | Counts and totals separated by currency |
| `/api/ui/rules` | Rule/status counts separated by saved ruleset identity |
| `/api/ui/invoice?file=…` | Saved invoice, outcome, rules, evidence and lifecycle |
| `/api/ui/pdf?file=…` | Persisted original PDF bytes from private Storage |

List filters: `q`, `action` (`PAGAR`, `ESCALAR`, `NO_PAGAR`), `lifecycle`
(`processed`, `extracted`, `processing`, `error`), `vendor`, `currency`, `page`
(default 1), `limit` (1–100, default 50). Supplier and invoice pages in the UI
use 25 items. Counts describe the entire filtered result, not just one page.
`query_ms` reports elapsed backend query time. Failed queries return an explicit
error, never fixtures. Backend connection errors are surfaced by the frontend.

The existing site access gate protects Next.js API routes. Server-side
`ENGINE_API_BASE`, then `NEXT_PUBLIC_API_BASE`, then `http://127.0.0.1:8010`
select the backend. Keep the existing backend configured and restart it after
code changes; this integration does not read `.env` or change credentials.

## Agent

The Agent view is the default desk home (`/` or `?view=agent`). Chat replies
stream over `/api/chat` as SSE (`delta` then `done`).

For known intents (escalations, ready to pay, do not pay, day numbers, rules,
named `*.pdf`), the server builds structured panels from the same `/api/ui/*`
query layer as Invoices/Summary. The model only phrases a short sentence from
those FACTS; it does not choose the panel. Open questions without a panel intent
still use the bounded read-only tools: `search_invoices`, `get_invoice`,
`get_rule_evidence`, and `invoice_totals`.

Search pages have 25 rows; tools reject traversal and unknown operations; the
tool loop allows at most six calls across four model rounds. Tool results over
32 KB are explicitly marked incomplete. Instructions distinguish recommendations
from approvals/payments and require invoice/rule identifiers. Missing provider
configuration still returns panels with a fixed fallback sentence when an intent
matched. Panel foot buttons call `POST /api/action`, which remains a no-op (409):
human decisions, payments and supplier sends are not connected yet.

## Retired demo surfaces

- Active lanes, summary, dossier, PDF, rules and chat no longer import desk-data.
- Build/dev hooks copy UI assets only and remove the old generated PDF/remittance
  export. Static `/desk/facturas/*` and `/desk/sepa.xml` return 410.
- Legacy invoice, review, finance, logs and rule pages redirect to the live desk;
  legacy dossiers redirect with the selected invoice. The old DataSource cannot
  fall back to mock values on a backend error.
- Fake batch outcomes and pretend action confirmations were removed. Uploads
  are not persisted/processed by this interface; batch replies say so. Human
  decisions, rule mutation, remittances and supplier sends remain unavailable.
- Historical mock fixtures remain in the repository for offline development;
  they are not the active Next.js data source. The separate old Python mock
  server is not a supported launcher for this live desk.

## Verification and measured speed

Measured 2026-09-19 against the existing local API and its configured database,
three consecutive HTTP GETs of the same processed invoice. No provider calls,
migrations or database writes were made for these timings.

| Query | Times (ms) | Median | Response size |
| --- | --- | --- | --- |
| Existing `/api/factura/{file}` | 9416, 8526, 7859 | 8526 ms | 29,110 bytes |
| New `/api/ui/invoice?file=…` | 176, 181, 182 | 181 ms | 55,620 bytes |
| New supplier page | 170, 161, 178 | 170 ms | 366 bytes |

**47.2x lower median latency for the detail query**, despite returning more
structured evidence. These are observed local API timings, not an SLA or a
concurrency/load test. The old explorer also loads `/flujo`; that additional
cost is excluded from this comparison. Repeat with:

```sh
uv run --locked --extra worker --extra backend python scripts/benchmark_ui_reads.py
```

Checks:

```sh
uv run --locked --extra worker --extra backend python -m pytest -q tests/decision_context/test_ui_queries.py tests/decision_context/test_backend_engine.py tests/decision_context/test_core_engine.py tests/test_core_engine_review.py
node frontend/scripts/test-live.cjs
cd frontend && npx tsc --noEmit
```

Database tests use disposable local Postgres and mocked Storage/providers.
The UI query test explicitly forbids Storage downloads. Additional tests cover
bounded pagination, canonical recommendation parity, missing records and newer
failed inputs superseding old evaluations. The frontend contract test mocks
fetch and the model, verifies real tool-result propagation, rejects unknown
operations/path traversal, and checks backend errors do not become demo data.

Browser checks use Orca's embedded browser: real supplier list, invoice dossier,
rule counts, review-disabled state, and 390px mobile layout. No paid live chat
calls were made; provider tool-calling behavior is not certified by the mock test.
