# Next.js invoice desk

The root route is now native React, translated from `desk/mock/index.html`. The mock remains the visual source of truth. No iframe, injected HTML, static invoice export or chat agent powers these pages.

- `/` and `/?view=invoices`: supplier groups, search, stage and recommendation filters, server pagination, invoice drawer.
- `/?view=summary`: persisted invoice counts, recommendation totals separated by currency, links into processing stages.
- `/?view=invoices&invoice=FILE`: addressable invoice detail. Existing `/facturas`, `/finanzas`, and `/expediente/FILE` redirects remain compatible. `/desk` and `/desk/index.html` redirect to React.

## Data and request cost

Existing authenticated Next API routes proxy the backend configured by `ENGINE_API_BASE` (default `http://127.0.0.1:8010`). These changes do not change SQL, storage, payment policy or provider configuration.

Supplier groups use `/api/lanes` (25 groups per page). Opening a group fetches `/api/invoices` (25 invoices per page). Opening an invoice fetches `/api/dossier`. PDF bytes and the full `/api/engine/factura/FILE/flujo` audit load only after their respective buttons are pressed. Summary uses one `/api/summary` request.

SWR deduplicates identical requests for five seconds, keeps keys separate across filters, and avoids focus polling and automatic error retries. Search is debounced 250 ms. Refresh explicitly revalidates records. Errors offer Retry; no mock fallback exists. Data is not cached in local storage.

Amounts with no currency are never added to known-currency totals. Missing values stay explicit. PAGAR is displayed as a recommendation, not evidence of payment. Disabled review stays DISABLED. The default stage is Processed, matching the source HTML; select All recorded to include incomplete and failed processing.

## Components and visual authority

`frontend/src/components/desk` owns React state and rendering. `frontend/src/app/desk.css` carries the reference's cream/brown palette, 228px sidebar, supplier lanes, compact mono figures, filters and right-side dossier, with responsive overrides and reduced motion. Inter and JetBrains Mono are loaded by Next. shadcn Button, Input and Dialog primitives preserve the source styling. The loading indicator is `thinking-orbs` from Libraries.dev. Chat and rules navigation are outside this implementation's scope.

## Verification

`cd frontend && npm run test:desk` runs four mocked DOM interaction tests without API/database/provider access: lazy supplier loading and filtering, currency-safe summary, retry after failure, and disabled-review dossier with deferred PDF/audit loading. TypeScript and focused ESLint checks pass. A production build passed from a temporary source copy excluding all `.env*` files.

Read-only checks against the running local backend and Next proxy returned 30 persisted invoices and 25 suppliers on 2026-09-19; those counts are a point-in-time observation. No database writes or paid provider calls were performed.

Browser runtime discovery returned no connected browsers. Desktop/mobile rendering, exact visual fidelity, and real-browser keyboard/focus behaviour remain unverified. DOM interaction tests and a passing build do not substitute for that visual acceptance.
