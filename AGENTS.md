# Working rules

- Never read `.env`; contextual review takes explicit configuration and does not load dotenv.
- Install the repository decision/review path with `uv sync --locked --extra worker --extra backend`.
- Task 3 focused checks: `uv run --locked --extra worker --extra backend python -m pytest -q tests/test_contextual_review.py tests/test_contextual_provider.py`.
- Task 1 integration regression checks: add `tests/decision_context` to that pytest command. Its Postgres tests use a disposable local instance and skip when the required binaries are unavailable.
- Lint new review code with `uv run --locked --extra worker --extra backend ruff check ingestion/contextual_prompt.py rules_ingestion/contextual*.py tests/test_contextual_review.py tests/test_contextual_provider.py`.
- Tests must not call paid APIs or shared databases. Scripted review fixtures are contract tests, not measured LLM accuracy.
- Keep `evaluation-result/1` immutable. Confirmed submitted/processed database duplicates reject; do not call them paid without payment evidence. Review artifacts never authorize payment.
- `rules_ingestion` is a repository API, not included in the ingestion wheel. Task 3 is not wired into `alberto/` or the frontend.
- `benchmark/schemas` is the extraction schema source of truth; packaged wheels carry copies under `ingestion/schemas`. Do not edit benchmark code/references/reports, existing provider prompts, or pipeline/config settings unless the task requires it.
- For packaging-only changes, refresh the lockfile with `uv lock --offline` without upgrading dependencies.
