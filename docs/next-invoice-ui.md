# Next.js invoice desk and audit journey

The root route is native React. The approved cream/brown palette, Inter typography and compact navigation derive from `desk/mock/index.html`. Invoice detail is now a full-page case history rather than a drawer or a JSON dump.

- `/` and `/?view=invoices`: supplier groups, search, stage/recommendation filters and server pagination.
- `/?view=summary`: persisted counts and totals separated by currency.
- `/?view=invoices&invoice=FILE`: receipt, extraction route, individual attempts, evaluation and its reasons, contextual review, final recommendation, unrecorded resolution/payment. Evidence belongs to its event and expands in place. The original PDF opens separately on request.
- Existing `/facturas`, `/finanzas`, `/expediente/FILE`, `/desk` and `/desk/index.html` links remain compatible.

The history covers the latest stored processing run, not every historical run. Missing timestamps remain explicit. Evidence capture time is never substituted for evaluation time. Supplemental detail timestamps/PDF availability are used only when both input and record version match the audit response. PAGAR is a recommendation; human resolution and payment are never inferred.

## Loading path

The frontend requests `/api/engine/factura/FILE/flujo` and `/api/dossier?file=FILE` concurrently. Native history replacement updates invoice selection without a server-component navigation first. SWR deduplicates requests for five seconds and avoids focus polling and automatic error retries. Refresh explicitly revalidates both records. Supplier groups/rows remain paginated at 25; search is debounced 250 ms.

Collapsed evidence, rule inputs and technical JSON are mounted only when expanded. This avoids serializing/rendering the complete audit plus repeated nested copies on first paint. No visual layout change is involved.

`backend.flujo.flujo` starts a fresh `artifact_session` for every request. It uses hash-verified canonical Postgres JSON where available, with existing Storage fallback, and deduplicates artifact metadata reads within that request only. It calls `archive.load_packet` to verify each record's identity and loads/verifies the artifacts displayed by the projection. It does **not** use `engine.load`, which performs a full forensic replay of all sources, originals and render bytes. The explicit core show/verification path retains full replay. Opening the UI therefore does not attest that every historical source byte was replayed.

Jobs and attempts use the existing `extraction_trace(input_id)` query, rather than scanning every job in the batch and querying attempts separately for each matching job. No schema, migration, provider, decision policy or immutable result change is involved.

## Evidence and validation, 20 September 2026

Read-only local baseline for one processed invoice, two sequential requests:

| Endpoint | Request 1 | Request 2 |
| --- | ---: | ---: |
| Audit flow | 6.754 s | 5.530 s |
| Detail | 0.336 s | 0.347 s |

These are local running-server observations, not production p95/load-test figures. The running Python process predates the change and requires restart using its existing explicit environment. This shell has no backend credentials; `.env` was not read. Optimized live timings are pending that restart.

Validation:

- 53 backend/core/review tests passed using disposable local Postgres and mocked Storage/providers.
- Focused audit regression verifies no full replay, no canonical-JSON Storage reads, unique metadata reads within a request, a fresh cache on the next request, and no batch-wide/per-job attempt queries.
- Seven mocked React interaction tests cover supplier filtering, currency handling, retries, immediate audit loading, missing records/timestamps, retry ordering, and deferred rendering of evidence/rule tables.
- TypeScript, focused ESLint, backend Ruff and production build passed. The build used an isolated copy excluding `.env*` files.

No paid providers or database writes against shared systems were used. Browser discovery has no connected browser; automated visual/real-browser navigation validation remains unavailable. The user approved the journey's visual direction before the speed-only pass.
