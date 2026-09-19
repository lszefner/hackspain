# Payment decisions: PAGAR / NO PAGAR / ESCALAR

The `payments` package turns already-extracted invoices plus normalized master
data and a frozen ruleset into a deterministic per-file decision. It is fully
offline: no provider calls, no database, no clock — `--as-of` is the only date.

## Runnable flow

```bash
uv sync --locked --extra worker --extra decision

# 1 · Build the frozen ruleset (offline: no JEV, no LLM fallback)
uv run --locked --extra decision python -m rules_ingestion.build_rules \
    --no-jev --no-llm --out /tmp/rules.json

# 2 · Extract + export with the existing pipeline (worker extras required)
uv run --locked --extra worker --extra decision invoice-agent ingest \
    --input facturas --interpreter jev
export BATCH_ID='your-batch-uuid'   # replace with the batch id printed by ingest
uv run --locked --extra worker --extra decision invoice-agent export \
    --batch "$BATCH_ID" --output output/export

# 3 · ERP snapshot — run the bridge in a SEPARATE terminal, credentials only
#     from the environment (value is in MANUAL_ERP_2009.md, never committed;
#     replace the literal below before running)
python3 alberto_erp.py            # http://127.0.0.1:8009
export ERP_USER=alberto ERP_PASSWORD='replace-with-manual-value'
uv run --locked --extra decision invoice-agent erp-snapshot \
    --base-url http://127.0.0.1:8009 --output /tmp/erp.json

# 4 · Decide (demo date)
uv run --locked --extra worker --extra decision invoice-agent decide \
    --export output/export \
    --rules /tmp/rules.json --sources rules_ingestion/sources.yaml \
    --erp-snapshot /tmp/erp.json --as-of 2026-09-19 --output /tmp/decision
```

Note `uv run --locked --extra ...` needs the extras listed on every command:
`--extra decision` alone drops worker deps for the `invoice-agent ingest`
steps.

`decision/` is a new immutable bundle: `outcomes.jsonl` (one row per input,
sorted by `file_id`), `summary.json`, `inputs.json` (the exact records the
engine consumed), `rules.json`, `master.json`, `schemas/` and
`decision-config.json` carrying `artifact_hashes` (SHA-256 of the exact bytes
of rules/master/inputs/each schema). Re-running into the same directory only
succeeds if every byte matches; a differing artifact raises instead of
overwriting.

## Replay

A captured bundle replays byte-identically with no XLSX, no ERP, no API — the
workbook and export inputs can even be deleted afterwards:

```bash
uv run --locked --extra worker --extra decision invoice-agent \
    replay-decisions --bundle /tmp/decision --output /tmp/replayed
```

`replay-decisions` verifies the recorded SHA-256 of `rules.json`,
`master.json`, `inputs.json` and every bundled schema before deciding; any
tamper aborts. `summary.json` reports `outcomes_equal_source` — whether the
replayed `outcomes.jsonl` equals the original byte-for-byte (the summary
itself contains paths, so only outcomes are compared).

A raw directory of `*.invoice.json` files (searched recursively, mapped
`sub/name.invoice.json` → `sub/name.pdf`) can be decided directly with
`--invoices DIR`; `--input-dir` adds a PDF inventory (matched
case-insensitively on `.pdf`, every PDF accounted for). That boundary imports
JSON as-is — it does **not** certify OCR/evidence, hence status `imported`.
`source_sha256` is the hash of the source PDF bytes when an inventory exists,
otherwise null; `invoice_sha256` in each result identifies the JSON invoice —
the two are deliberately distinct.

## Python API

```python
from payments import DecisionEngine, decide_files
from payments.master import master_from_lookups

master = master_from_lookups(proveedores, pedidos, erp_snapshot, as_of="2026-09-19")
results = DecisionEngine(ruleset, master).decide(records)   # records: {file_id, invoice, status, source_sha256, ...}
summary = decide_files(rules=..., sources=..., as_of=..., output=..., export_dir=...)
```

External duplicate history is supplied as sets on the master:
`seen_invoice_keys = {(invoice_number, vendor_id)}` and
`seen_amount_date = {(Decimal("121"), "2026-09-01")}` — the soft key's amount
is a `Decimal`, matching the normalized invoice total.

## Tradeoffs

1. **Escalate, never invent.** Anything untrusted — failed/needs_review
   status, extraction error, invoice `issues`, corrupt or hash-mismatched
   artifacts, malformed checks — gates straight to `ESCALAR` and is never
   promoted into a hard `NO PAGAR`. An enabled rule whose canonical or
   `condition` is unsupported contributes a review row (a trusted hard
   `NO_PAGAR` from another rule still outranks it); generated rule code is
   never executed.
2. **VAT is only what is labelled.** `iva` aggregates rows whose label starts
   with `IVA`/`VAT` with all amounts present; a single shared rate enables the
   `base * rate / 100` check. Mixed rates (no per-rate base), missing rates, or
   non-VAT rows like IRPF escalate rather than guess.
3. **All duplicate copies are withheld.** Hard duplicates `(invoice_number,
   resolved vendor)` make *every* file in the group `NO PAGAR` — never a
   "first payable" copy. Soft duplicates `(total, date)` escalate all members,
   and two clean invoices claiming the same `pedido` escalate together —
   this prevents two approvals for one order but never arbitrarily selects
   a winner. Records with readiness issues do not enter groups, so a credit
   note or a missing number cannot poison a clean invoice; blank keys never
   group.
4. **ERP is the only pay-state authority; ambiguity reconciles, not merges.**
   Any `PAGADA` row is a hard stop; missing/unknown state or conflicting
   identities/amounts produce reconciliation reviews instead of
   last-write-wins. With no snapshot, order state is simply unknown — the
   Excel `estado` column is never a fallback. A complete, internally
   consistent download is required: no partial snapshot ever reaches a
   decision.
5. **`require_active` and `check_nif_control_digit` are advisory.** The
   workbook schema has no `active` column and synthetic NIFs mostly fail the
   real control digit, so neither gates payment; master membership plus IBAN
   match are the hard gates.

## Limits

- This decides; it never pays. `NO PAGAR`/`PAGAR` are recommendations.
- Current sample outcomes (`output/automatic-invoices`) escalate for
  extraction uncertainty or date/reconciliation checks — the 2026-09-19
  demo `as_of` also marks historical invoices overdue; none of this is
  evidence of full-corpus accuracy.
- Final challenge artifacts require a later full extraction run over all 500
  PDFs; this change ships the decision layer, not fabricated 500-doc outputs.
