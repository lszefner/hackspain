# Next PR: connect the frontend to the canonical engine

## Scope and priority

This is a required integration follow-up, not frontend implementation in the current PR. Keep the canonical business flow in `backend.run_revision` and `rules_ingestion.engine`; do not introduce another frontend or HTTP-specific evaluator. Database migration/application credentials are managed separately by the project owner.

The current frontend cannot be connected by changing `NEXT_PUBLIC_API_BASE` alone. Its adapter speaks the SQLite-backed `alberto web` contract, while `backend.server` exposes the new Supabase-backed review contract.

## Current contract mismatch

### Existing frontend consumers

`frontend/src/lib/source/alberto.ts` expects:

| Operation | Route |
| --- | --- |
| Published rules and active version | `GET /api/normas` |
| Summary indicators | `GET /api/kpis` |
| Decision tiles | `GET /api/baldosas` |
| Invoice list | `GET /api/facturas` |
| Events | `GET /api/eventos` |
| Decision reasons | `GET /api/motivos` |
| Invoice dossier | `GET /api/expediente/{file_id}` |
| Review queue | `GET /api/bandeja` |
| Cost information | `GET /api/coste` |
| Comparison of rule versions | `GET /api/diff` |
| Run history | `GET /api/partes` |
| Dependency health | `GET /api/salud` |

The upload actions in `frontend/src/app/subir/actions.ts` also expect:

- `GET /api/documento/{sha256}` for content identity and prior results.
- `POST /api/subir` with raw PDF bytes and `X-File-Name`.
- `POST /api/reprocesar` with a document ID.

The complete response types are in `frontend/src/lib/types.ts`. Do not assume that matching route names also means matching response shapes.

### Current canonical backend

`backend.server` currently offers:

- `GET /api/resumen`.
- `GET /api/factura/{file_id}`.
- `GET /api/estado`.
- `GET /api/ejecucion/{request_key}`.
- `POST /api/lanzar` with a request key and selected files.

The runner currently accepts files already present in an input directory. The canonical server does not yet provide the existing frontend's upload/reprocess transport contract. The live store is `PostgresResultsStore`, not the legacy SQLite `ResultsStore`.

## Recommended integration boundary

Keep the pages consuming `DataSource`, but provide an engine-backed implementation of that interface and matching typed backend responses. Compatibility routes may be used to reduce page changes. They must delegate to the canonical runner and persisted records, never to `alberto`'s separate decision engine or client-side rule calculations.

Before implementation, freeze a route/response matrix covering every consumer above. Decide which routes remain compatibility aliases and which consumers move to canonical routes. Missing endpoints must not silently return mock data.

## Required semantic contract

Represent these independently:

1. **Run execution:** running, completed, partial, failed, or unknown.
2. **Extraction:** completed, needs review, failed, or unknown.
3. **Deterministic evaluation:** preliminary decision, rule results, completeness, approval eligibility, and outstanding findings.
4. **Contextual review:** status, assessments, citations, limitations, and `attention_required`.
5. **Human resolution/payment evidence:** separate records and authority. The current engine does not execute or authorize payment.

`PAGAR` is a preliminary recommendation. A technically completed run or `hecha` record is not proof that the invoice has been cleared for approval, much less paid. In particular:

- A failed, incomplete, challenged, or attention-required review must remain visible in the review workflow.
- Bounded evidence-view reviews intentionally remain `INCOMPLETE`; their omitted context must not be presented as fully reviewed.
- A confirmed processed/submitted duplicate is not necessarily a previously paid invoice.
- Render original rule statuses, including `BLOCKED`, `UNSUPPORTED`, and `NOT_APPLICABLE`; do not disguise them as successful checks.
- Do not mutate the immutable deterministic evaluation to incorporate model or human opinions. Link a separate disposition/resolution record.

Any derived UI disposition must be explicitly specified and tested. It must not grant payment authority that the engine does not possess.

## Identity, provenance, and version selection

- Separate PDF content identity, extraction input/batch IDs, run request keys, evaluation record IDs, and review record IDs.
- Filenames are display/provenance information, not sufficient business-invoice identity.
- A ruleset needs its exact artifact hash as well as its human-readable version. Two different generated artifacts can both say `v3`.
- Expose the frozen evaluation date, source/snapshot identities, evaluator implementation identity, and contextual prompt/model identity.
- Dossiers must link to the exact artifacts behind the displayed decision, not today's workbook or newest extraction.
- Historical runs must remain navigable after reprocessing and after local PDF files are removed.
- Historical replay may require the original evaluator release. Do not silently recompute old results with current code and present them as the original decision.

## Upload and reprocessing requirements

- Persist original bytes and content identity before any paid work.
- Validate filename, PDF content, size, and path handling at the backend boundary.
- Accept a stable, explicit request key. Identical retries reuse the intent; changed input under the same key is an error.
- Surface running/unknown intents rather than starting another paid call because a browser timed out.
- Distinguish rereading a document, evaluating an existing parse under another ruleset, and rerunning contextual review. These are different operations with different costs and provenance.
- Do not block or allow reprocessing based on `PAGAR` being interpreted as proof of payment.
- Expose a status lookup that works after a process restart and does not depend on browser memory or local filenames.

## Database and delivery requirements

- All business results and history come from Postgres and private artifact storage. No SQLite or in-memory production fallback.
- Add pagination/filtering to collection APIs; avoid downloading full evidence packets just to render lists.
- Detail/artifact access must have an explicit authorization boundary. Provider and Supabase secret keys stay backend-only.
- Dependency failures produce real error/degraded states. `frontend/src/lib/data.ts` currently falls back to mock data; production mode must not do that silently.
- Report unavailable costs as unknown, not zero. Health must reflect real dependency readiness rather than a process-local busy flag.

## Acceptance checklist for the next PR

- [ ] Route and response matrix approved against every current frontend consumer.
- [ ] HTTP contract tests exercise actual request/response serialization, not only direct handler methods.
- [ ] A persisted evaluation and review can be displayed with correct status separation and resolvable citations.
- [ ] Upload, duplicate lookup, processing status, and explicit reprocessing use the same canonical engine.
- [ ] Two identical client submissions cannot duplicate paid processing.
- [ ] Failed/incomplete review of a preliminary `PAGAR` cannot appear as cleared or paid.
- [ ] Old runs remain readable after new runs; rule-version comparisons use exact artifact identities.
- [ ] No frontend rule execution or production mock fallback remains on this path.
- [ ] Live migration/credentials and a controlled smoke test are completed with the project owner's approval.
