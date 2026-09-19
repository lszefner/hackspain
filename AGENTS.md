# Working rules

- Install extras: `uv sync --locked --extra worker`. Worker commands and the
  async API need the `worker` optional dependency group; the base install
  covers `Contracts`, `blank_invoice`, and the offline `fixture` command.
- Run checks: `uv run --locked --extra worker pytest -q tests` and
  `uv run --locked --extra worker ruff check <files>`.
- For packaging-only changes, refresh the lockfile with `uv lock --offline`
  without upgrading dependency versions.
- Tests must not call paid APIs or shared databases. Postgres tests use the
  disposable local instance in `tests/conftest.py` and skip when
  `initdb`/`postgres` binaries are unavailable.
- Never read `.env`; the API performs no dotenv loading and the host
  configures environment explicitly.
- `benchmark/schemas` is the schema source of truth; packaged wheels carry
  immutable copies under `ingestion/schemas`. Do not edit schemas, benchmark
  code/references/reports, provider prompts, or `pipeline`/`config.py`
  settings unless the task requires it.
