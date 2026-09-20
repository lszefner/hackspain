# Backend data explorer

Open http://localhost:3000/backend in the existing Next.js frontend. The existing
site access gate applies. The page reads `/api/resumen`, `/api/salud`, and
`/api/estado`; selecting an invoice loads `/api/factura/{file_id}` and
`/api/factura/{file_id}/flujo`. Every response field can be expanded, including
full JSON. Search and status filters apply to the invoice list. This is the
initial data inspection surface; lifecycle filtering is not implemented yet.

The Next.js `/api/engine/[...path]` proxy forwards only supported GET endpoints
(including `/api/ejecucion/{request_key}`), preserves backend HTTP error statuses,
and disables caching. It uses `ENGINE_API_BASE`, then `NEXT_PUBLIC_API_BASE`,
then `http://127.0.0.1:8010`. Keep the configured backend running. No backend
restart or new backend endpoint is required by this change.

The explorer does not initiate processing or payments. The list's decision is
the evaluator decision, not a payment record; the full flow exposes the separate
`salida`, `resolucion`, and `pago` fields as stored/projected by the backend.
