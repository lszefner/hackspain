# Agent PDF upload using current YAML

Verified locally against live Supabase on 2026-09-20 using `make api-yaml`.

- Browser attachment and Send: `2026-01-17_P011.pdf`.
- Run: `agent-1789892526219-4dcoo2`, completed; one stored result, zero failed.
- Rules: current balanced YAML plus mapped workbook, compiled without paid
  authoring or historical discovery caches, then pinned with frozen sources.
- Ruleset SHA-256: `892e312f2e51737ab06fdab5847057eb6bae42e28f5b198c491f51b76079971c`.
- Verified against the persisted run reference: NIF checksum false,
  payment-term enforcement false, contextual review disabled.
- Postgres contains canonical run input and result payloads at `postgres/`
  addresses. Original/source bytes and ruleset bytes use private Storage,
  referenced by immutable artifact metadata in Postgres.
- Final browser recommendation: `NO_PAGAR` because a matching submitted
  invoice already exists. Currency evidence is also missing; this is a
  completed evaluation, not approval or payment.
- Saved engine time before final summary: 21.89 seconds; this excludes final
  commit and browser summary rendering.

The test first exposed source MIME differences between generated and pinned
rules. Both backend paths now label original rule-source bytes as
`application/octet-stream`; existing artifacts are preserved. A regression
test runs generated then YAML-pinned processing against disposable Postgres.

Unknown runs now stop the UI when the engine reports idle, preserving the
backend error without retrying uncertain work. A temporary unknown state
while the engine is busy still polls.

Validation: 86 backend/policy tests passed before the MIME fix; the 31-test
backend/YAML suite passed after it, including the new regression. Eight
frontend tests, TypeScript, Ruff and diff checks passed.

Restart `make api-yaml` after changes to YAML/workbook inputs. Runtime bundles
are ignored local preparation files, not a replacement for database persistence.
