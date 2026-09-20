# Backend JSON API contract

`python -m backend.server` — a stdlib-only JSON API that exposes the invoice
review pipeline (persisted Postgres rows + immutable engine artifacts) to the
Next.js frontend. CORS is `Access-Control-Allow-Origin: *`; every response is
`application/json; charset=utf-8`. The API is read-only except `POST
/api/lanzar`; it never recomputes rules and never calls a provider while
serving a request.

## Running it

For the agent upload flow using the current YAML policy, run `make api-yaml`.
It compiles `rules_ingestion/profiles/balanced.yaml` and the mapped workbook
locally, without JEV/LLM rule authoring or historical discovery caches. NIF
checksum and payment-term enforcement remain disabled by that profile.
Uncompiled/unsupported conditions retain the evaluator's review behavior.
Restart this target after editing policy or workbook inputs.

The launcher freezes exact source bytes under ignored `backend/data/yaml-rules/`
and pins the generated rules for the normal persisted engine. Runs, extraction,
evaluations, recommendations and canonical audit JSON still persist in Postgres;
original/source artifacts remain in private Supabase Storage. Contextual review
is explicitly disabled. This does not clear or retry unknown generation attempts.

```bash
uv sync --locked --extra worker --extra backend
make erp          # terminal 2: local ERP on :8009 (seeds caja/ if needed)
make api          # terminal 1: API on http://127.0.0.1:8010
make api-status   # -> GET /api/salud
```

`make api` loads `.env` into the recipe shell (`set -a; . ./.env`) exactly like
`centralita` does — that is shell-level env export only; the Python process
itself never reads dotenv. Frontend base URL: `NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010`.

Read-only startup and GET requests require only `SUPABASE_DB_URL`. Storage and
provider credentials are initialized lazily when a revision is launched.

Environment variables for launching revisions (names only):

- `REVISION_REVIEW_ENABLED` — defaults to `false`. Runs persist disabled review
  and use the evaluator recommendation; no contextual provider is called.
  Set `true` explicitly to opt back into review. Recommendations never execute payment.
- `SUPABASE_URL`, `SUPABASE_SECRET_KEY`, `SUPABASE_DB_URL` — Postgres +
  private Storage bucket (`SUPABASE_STORAGE_BUCKET`, default
  `invoice-ingestion-private`).
- `HELMCODE_API_KEY`, `HELMCODE_BASE_URL`, `HELMCODE_DEEPSEEK_MODEL` —
  extraction/interpreter credentials; `HELMCODE_VISION_MODEL`,
  `HELMCODE_LAYOUT_MODEL` optional.
- `JEV_API_KEY` — rule generation (only when a run is launched without a
  pinned ruleset).
- `REVIEW_ENDPOINT`, `REVIEW_MODEL`, `REVIEW_API_KEY` — optional overrides to
  run contextual review on a different model/account.
- `REVISION_INPUT_DIR` — optional override for the facturas directory
  (default resolves via `backend.caja_paths.facturas()`).
- `REVISION_RULESET_PATH`, `REVISION_RULE_SOURCES`, `REVISION_EVALUATION_DATE`
  — optional run defaults.

Server flags: `--host` (default `127.0.0.1`), `--port` (default `8010`).

## Postgres-only audit reads

The backend always writes canonical audit JSON to Postgres. There is no
storage-mode environment switch. Original PDFs and exact
source bytes remain in private Storage.

All GET responses load audit JSON exclusively from `ingestion.artifacts.payload`.
Historical Storage-addressed artifacts work when their canonical JSON payload
is already in Postgres; no address rewrite or backfill is needed for those rows.
A missing payload never falls back to Storage: invoice detail and run status
return **503** with `postgres_audit_missing` (or an integrity error); the flow
endpoint preserves its section-level error contract and escalates the output
when required audit sections are unavailable.

The API verifies JSON hashes, sizes, references and record identities. It projects
stored evaluations; it does not replay the evaluator or download original
sources. Use `rules_ingestion.engine_cli show --record-id er_HASH` for full
forensic source verification. No cross-request cache can mask a new run or a
changed artifact. Summary reads omit full reviewer findings; detail retains them.

Query budgets: summary **1**, detail **3**, full flow **4**, run status **2**;
Storage HTTP calls **0**. See [read-path validation](postgres-api-reads.md).

## Routes

### GET /api/salud

Dependency health. Always 200; never raises.

```json
{"ok": true, "postgres": true, "storage_bucket": null,
 "facturas_dir": "/path/caja/facturas", "facturas_en_disco": 500,
 "procesando": false, "error": null}
```

- `postgres` — repository table probes.
- `storage_bucket` — always `null`: GET requests do not contact Storage.
  The field is retained for existing clients. Ingestion checks the private bucket
  when its writer is initialized.
- `ok` = `postgres`; this checks API read availability, not ingestion readiness.
- `error` — exception class name when any check raised, else `null`.

### GET /api/resumen

All known invoices (disk ∪ store) with derived state.

```json
{"total": 1, "conteo": {"pendiente": 0, "procesando": 0, "hecha": 1, "error": 0},
 "decisiones": {"PAGAR": 0, "ESCALAR": 1, "NO_PAGAR": 0},
 "facturas": [{"file_id": "invoice.pdf", "estado": "hecha", "decision": "ESCALAR"}],
 "lote_tamano": 20, "procesando": false, "error": null}
```

### GET /api/factura/{file_id}

`file_id` is the exact PDF filename (NFC). `../`, `/`, `\` → **422**
`{"error": "invalid_file_id"}`. Unknown file (no row, not on disk) → **404**
`{"error": "factura_no_encontrada"}`. On disk but never processed → 200 stub
with `estado: "pendiente"`, `decision: null`, `checks: []`, `campos: {}`.

Processed row:

```json
{"file_id": "...", "estado": "hecha", "decision": "ESCALAR",
 "checks": [{"rule_id": "...", "verdict": "NEEDS_REVIEW", "reason": "..."}],
 "campos": {"invoice_number": "...", "vendor_id": "...", "total": "..."},
 "error": null, "evaluation_record_id": "er_...", "review_record_id": "er_...",
 "evaluation_result": { ... }, "contextual_review": { ... },
 "review_status": "INCOMPLETE", "attention_required": true}
```

`evaluation_result` is the evaluator artifact —
`rules_ingestion/evaluation_result.schema.json`. `contextual_review` is the
review body — `REVIEW_SCHEMA` in `rules_ingestion/contextual_contracts.py`
(`status` COMPLETED|INCOMPLETE|FAILED, `rule_reviews`, `findings`,
`attention_required`, `payment_authorized` always false).

### GET /api/factura/{file_id}/flujo

Full processing flow of one invoice — the authoritative drill-down. Same
validation as `/api/factura` (422 invalid, 404 not found). Response keys
exactly:

```json
{"file_id": "...", "identidad": {...}|null, "ejecucion": {...}|null,
 "extraccion": {...}|null, "evaluacion": {...}|null, "revision": {...}|null,
 "salida": {"verdict": "...", "basis": "..."}, "resolucion": null, "pago": null,
 "etapas": [{"etapa": "...", "estado": "...", "at": null, "ref": null}, ...]}
```

- `identidad` — `{input_id, batch_id, sha256, bytes, recibida_at}`.
- `ejecucion` — `{request_key, state, error, captured_at,
  evaluation_date_policy, rule_generation ("pinned"|"generated"|"pending"),
  snapshots}`; snapshot dicts carry `{kind, captured_at, as_of, asserted_by,
  authoritative_for, scope, availability}` (payload artifacts are not
  exposed).
- `extraccion` — `{status, error {code, unknown_outcome}|null, cost_usd,
  ruta ("deterministic"|"vision"|null), determinista {attempted, accepted,
  gaps}|null, por_que_vision, paginas, trabajos, factura, checks, gaps}`.
  `trabajos` is every pipeline job (original/render/reading/interpretation)
  with `{id, stage, provider, model, state, attempt_count, max_attempts,
  extra, config_version, prompt_version, adapter_version, schema_hash,
  artifact_id, last_error, created_at, intentos}`; each `intentos` entry is
  `{attempt_number, status, started_at, finished_at, latency_seconds, usage,
  provider_request_id, error, raw_artifact_ids}`. Job `settings.config` is
  never exposed — only `extra`.
- `evaluacion` — `{record_id, evaluation_id, evaluation_date, captured_at,
  interpreter, decision, resultado, fuentes, rule_source_lineage}`.
  `resultado` is the full evaluator artifact
  (`evaluation_result.schema.json`); `fuentes` summarizes each decision
  source (kind, sha256, captured_at, availability, scope...).
- `revision` — `{record_id, status, attention_required, error, reviewed_at,
  rule_reviews, findings, provider, model}`; the review body follows
  `contextual_contracts.py` `REVIEW_SCHEMA`.
- `salida` — `{verdict, basis}`, the deliverable verdict (see semantics).
- `etapas` — exactly seven entries in order: `recibida, extraida, evaluada,
  revisada, emitida, resuelta, pagada`, each `{etapa, estado
  ("hecha"|"pendiente"|"error"|"no_registrada"), at, ref}`.
- A missing/corrupt artifact degrades its own section to `{"error":
  {"code": ...}}` — the endpoint never 500s on stored-data problems.

### GET /api/ejecucion/{request_key}

Run summary. Unknown key → **404** `{"error": "run_not_found"}`. Finished run:

```json
{"request_key": "intent-1", "state": "completed", "batch_id": "...",
 "revision_counts": {"stored": 1, "failed": 0},
 "files": [{"file_id": "...", "evaluation_record_id": "er_...",
            "review_record_id": "er_...", "evaluation_date": "2026-09-19",
            "decision": "ESCALAR", "review_status": "INCOMPLETE",
            "attention_required": true, "error": null}]}
```

`state` ∈ completed | partial | failed | running | unknown. While a run has
no result artifact the body is `{request_key, state, batch_id, error}`.

### GET /api/estado

`{"procesando": bool, "error": string|null}` — is a batch running, last
background error class name.

### POST /api/lanzar

`application/x-www-form-urlencoded`. `request_key` required (1–200 chars).
`objetivo=una` + `file_id` launches that one PDF; otherwise the next batch of
up to `lote_tamano` (20) pending files. Response `{"ok": bool, "procesando":
bool}`; `ok:false` when nothing pending or a run is already in flight.
Missing/invalid fields → **422** `{"ok": false, "error":
"invalid_run_request"}`. The work runs in a background thread; poll
`/api/estado` / `/api/ejecucion/{request_key}` / `/api/resumen`.

## Semantics (policy — read before rendering)

- `estado` is **derived**, not stored: `pendiente` (on disk, never run),
  `procesando`, `hecha`, `error`.
- `decision` is the **evaluator's preliminary decision**. The deliverable
  verdict is `flujo.salida.verdict` (same policy as
  `export_outcomes.decide_output` / `outcomes.jsonl`): an unreviewed or
  challenged `PAGAR` becomes `ESCALAR` there (`basis` explains why:
  `no_evaluation | not_processed | evaluator | review_challenged |
  review_unavailable | evaluator_confirmed_by_review`).
- `resolucion` and `pago` are always `null`, and `etapas` `resuelta`/`pagada`
  are always `no_registrada`: the engine never authorizes or records payment.
- `extraccion.por_que_vision` lists the deterministic-gate gaps (from
  `ingestion/deterministic.py` `accept()` plus extractor gaps, e.g.
  `missing:/supplier/tax_id`, `check:arithmetic`,
  `no_text_layer_or_cascade_off`); it is empty when the deterministic route
  was accepted.
- Repeating a `request_key` never re-issues paid work; identical retries
  return the stored run.

## Mapping to the existing `DataSource`

| `DataSource` / UI field | Feed from |
|---|---|
| `facturas()` → `FacturaFila.etapa` | `/api/resumen` rows + `/api/factura/{id}/flujo` `etapas` |
| `expediente()` | `/api/factura/{id}` (`checks`, `campos`, `evaluation_result`, `contextual_review`) + `/flujo` for the pipeline timeline |
| `bandeja()` | `/api/resumen` rows whose `decision`/`flujo.salida.verdict` is `ESCALAR` |
| `salud()` | `/api/salud` |
| `partes()` | `/api/ejecucion/{request_key}` per run (`revision_counts`, `files`) |

Not implemented yet — keep the mock or feature-flag them: `/api/normas`,
`/api/kpis`, `/api/eventos`, `/api/coste`, `/api/diff`, `/api/documento`,
`/api/subir`, `/api/reprocesar`. See
`docs/frontend-engine-contract-next-pr.md` for the planned contract.

## Example payloads

Captured from the disposable test runtime (mocked providers, local Postgres —
no paid calls) under `docs/examples/backend-api/`:

- `resumen.json` — `GET /api/resumen`
- `factura.json` — `GET /api/factura/invoice.pdf`
- `factura.flujo.json` — `GET /api/factura/invoice.pdf/flujo` (full pipeline:
  vision route, evaluation, INCOMPLETE review, ESCALAR output)
- `factura.flujo.extraction-failed.json` — same route when extraction failed
  (`extraida` stage `error`, `evaluada` `pendiente`, `salida` ESCALAR /
  `no_evaluation`)
- `salud.json` — `GET /api/salud`
- `ejecucion.json` — `GET /api/ejecucion/intent-1`

A typed fetch client mirroring every route lives in
`frontend/src/lib/engine/` (`createEngineClient` + `types.ts`); it is not
wired into `data.ts` yet.
