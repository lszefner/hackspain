# engine client

Typed `fetch` client for the engine JSON API (`python -m backend.server`).
The full route contract, semantics and example payloads live in
[docs/backend-api.md](../../../../docs/backend-api.md).

```ts
import { createEngineClient } from "@/lib/engine/client";

const engine = createEngineClient(); // reads NEXT_PUBLIC_API_BASE
const salud = await engine.salud();
const flujo = await engine.flujo("invoice.pdf");
// flujo.salida.verdict is the deliverable verdict (PAGAR/ESCALAR/NO_PAGAR)
await engine.lanzar({ requestKey: "intent-42", fileId: "invoice.pdf" });
```

Non-2xx responses throw `EngineApiError` with `status` and the server's
`error` code. Deep engine artifacts (`evaluacion.resultado`, review bodies)
are typed as `Record<string, unknown>` — see `types.ts` for the schema
pointers.

Pages consume this through `DataSource` via
[`../source/engine.ts`](../source/engine.ts), selected automatically when
`NEXT_PUBLIC_API_BASE` points at a live `backend.server` (`/api/salud`
reports `postgres`).
