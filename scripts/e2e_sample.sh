#!/usr/bin/env bash
# End-to-end sample run: 10 first + 10 middle + 10 last PDFs of caja/facturas.
#
# Env requirements follow the code at HEAD (post-74c9e91 rule authoring runs on
# the Helmcode credentials, so DEEPSEEK_API_KEY is not used; REVIEW_ENDPOINT /
# REVIEW_MODEL / REVIEW_API_KEY remain optional overrides that default to the
# Helmcode account -- see rules_ingestion/contextual_provider.py).  Values are
# never printed.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! curl -fs http://127.0.0.1:8009/erp/estado >/dev/null; then
    echo "ERP is not up; start it with \`make erp-fast\`" >&2
    exit 1
fi

missing=()
for var in SUPABASE_URL SUPABASE_SECRET_KEY SUPABASE_DB_URL \
           HELMCODE_BASE_URL HELMCODE_API_KEY HELMCODE_DEEPSEEK_MODEL \
           JEV_API_KEY; do
    if [ -z "${!var:-}" ]; then
        missing+=("$var")
    fi
done
if [ "${#missing[@]}" -gt 0 ]; then
    echo "missing env vars: ${missing[*]}" >&2
    exit 1
fi

mapfile -t FILES < <(uv run --locked python - <<'PY'
from pathlib import Path
names = sorted(p.name for p in Path("caja/facturas").glob("*.pdf"))
n = len(names)
seen = set()
for name in names[:10] + names[n // 2 - 5:n // 2 + 5] + names[-10:]:
    if name not in seen:
        seen.add(name)
        print(name)
PY
)

REQUEST_KEY="${REQUEST_KEY:-e2e-30-$(date +%Y%m%d-%H%M%S)}"
EVALUATION_DATE="${EVALUATION_DATE:-$(date +%F)}"
mkdir -p /tmp/e2e

rc=0
uv run --locked --extra worker --extra backend python -m backend.run_revision \
    "${FILES[@]}" \
    --request-key "$REQUEST_KEY" \
    --evaluation-date "$EVALUATION_DATE" \
    --input-dir caja/facturas \
    --sources rules_ingestion/sources.yaml \
    | tee /tmp/e2e/run_summary.json || rc=$?

uv run --locked --extra worker --extra backend python -m backend.export_outcomes \
    --request-key "$REQUEST_KEY" \
    --format traced --out /tmp/e2e/outcomes_sample.jsonl

exit "$rc"
