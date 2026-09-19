# Two difficult invoices: current product benchmark

Run date: 19 September 2026. **The current pipeline failed to produce a complete, usable invoice on either case. It also marked one near-empty extraction as completed.**

## Scope and method

- Fresh live run of the current product: PDF rendered at 200 DPI → fal `fal-ai/got-ocr/v2` → Jev `jev-choice-0.2`, resolved model `jev-1.13.0` → existing validation and Supabase persistence. Concurrency 2.
- Batch: `42f78b82-dca2-46ce-85c9-232ecd90215b`. One reading attempt and one interpretation attempt per invoice; no retries or reuse events. Two OCR submissions and 19 Jev requests.
- Selected `scan_023.pdf` and `scan_025.pdf` from the last filename group in the existing 50-file selection. “Last” means the existing positional selection, not most recent invoice date or ingestion time. They were chosen for visible difficulty, not by scoring every invoice and taking the worst results.
- `scan_023`: blur, smudged identifiers/bank details, small OK mark, faint mirrored second invoice. `scan_025`: severe dark shadows and a mirrored second invoice with an URGENTE stamp.
- Inspected both complete rendered source pages before inspecting predictions. Scored against the frozen existing draft references, with their uncertainty exclusions. Both are held-out examples; no model, prompt, candidate rules, production code, or references were changed.
- Scores are **provisional, not human-verified ground truth**, and cannot be extrapolated to all 500 invoices. Source hashes and all exported artifact hashes were checked. Product/schema hashes remained unchanged during the test.
- The full-run importer requires exactly 50 documents. This two-case audit calls its existing `score_invoice` and `score_reading` functions directly, without changing scoring rules or labelling real invoices as synthetic.

## Results

| Measurement | scan_023.pdf | scan_025.pdf |
|---|---:|---:|
| Correct present core facts, excluding annotations and uncertain fields | **5/20 (25%)** | **0/20 (0%)** |
| Correct present facts including scored annotation fields | 5/24 (20.83%) | 1/23 (4.35%) |
| Extracted line items / visible foreground line items | **0/3** | **0/2** |
| Extracted tax rows / visible foreground tax rows | **0/1** | **0/1** |
| Entire document correct | No | No |
| Invoice JSON passes schema | Yes | Yes |
| Runtime status | needs_review | **completed** |
| Reported mapped-block coverage | **100%** | **100%** |
| OCR stage time | 101.34 s | 101.25 s |
| Interpretation stage time | 65.51 s | 13.64 s |
| Reading start to interpretation completion event | 171.20 s | 118.41 s |
| Jev HTTP requests | 17 | 2 |
| fal submissions / GET requests | 1 / 55 | 1 / 55 |
| Reported Jev input / output tokens | 261,264 / 81,055 | 78,712 / 30,230 |
| Actual monetary cost | Unknown | Unknown |

Combined: **5/40 core facts (12.5%)**, **0/5 lines**, **0/2 tax rows**, **0/2 complete documents**. Full-reference present-value recall is 6/47 (12.77%); its single success for scan_025 is the footer *kind*, not invoice content. These metrics exclude absent reference values from recall, so empty fields do not inflate accuracy.

The full CLI invocation took **178.57 seconds**, including startup/preflight and batch processing; export/scoring are excluded. Individual spans above exclude initial setup/render/upload and final outcome persistence. Stage times overlap because concurrency was 2; do not add them to estimate batch elapsed time. One two-document run establishes neither throughput at scale nor p95 latency. Token totals are provider-reported usage, not a verified bill; cost remains null.

## What happened on scan_023

The five correct core fields were document type, invoice number `2026/22608`, date `2026-04-29`, currency EUR, and total **2480.50** (serialized `2480.5`, numerically equivalent).

The OCR captured recognizable values for the three lines, VAT and total, but flattened the entire invoice into one text line. It inserted spaces within words and identifiers, damaged the supplier name, dropped the customer name and bank account, and read the base as `2.050.00`.

The final JSON:

- Supplier name: `Catering Hermanos PicoS. L.` instead of `Catering Hermanos Pico S.L.`.
- Order reference: `PO- 2026-0726` instead of `PO-2026-0726`.
- Customer tax ID: `A 58231074` instead of `A58231074`.
- Missing customer name `Banco Miralmar S.A.` and supplier location Valencia.
- Missing all three billed rows: Servicio mensual 683.33; Mantenimiento trimestral 683.33; Suministro pedido 683.34.
- Missing taxable base **2050.00** and VAT **21%, 430.50**.
- No annotations: the visible OK mark and footer are not represented; bleed-through is also not described or flagged.
- Bank account and supplier tax ID remain null. Their draft references are explicitly uncertain, so they were excluded from accuracy scoring rather than treated as confirmed missing facts.

**Additional structural loss after OCR:** the recorded Jev answer is `group/0/role = other`. There is only one candidate line group for the whole page, so no line/tax field questions are generated. The existing grouping depends on OCR newlines. This explains why numerals present in OCR still disappear from structured rows. The `needs_review` issues name supplier tax ID and taxable base; they do not explicitly identify the lost three rows, missing VAT row, or missing customer.

## What happened on scan_025

OCR returned only this sentence four times:

> Documento generado por el sistema de facturación del proveedor.

The source visibly contains supplier Limpiezas Turia S.L., invoice `2026/27401`, date `22/03/2026`, order `PO-2026-0728`, supplier/customer tax IDs, an IBAN, two 320.00 lines, base **640.00**, VAT **134.40 at 21%**, and total **774.40 EUR**. None survived into OCR or structured fields.

Jev selected document type `other`, left every main scalar null, returned empty line/tax arrays and preserved the four repeated sentences as four footer annotations. It produced no issues. The source has one foreground footer and a faint mirrored background footer; four identical normal-reading footers do not accurately represent that layout. The frozen-reference scorer counts extra annotation fields as unsupported; this is a duplication failure, not fabricated invoice amounts.

**The system then marked this result `completed`. Every validation check passed.**

## Structure and validation gaps

1. **No effective OCR completeness gate.** Nonempty text is enough to proceed. Four footer sentences satisfy it even when the whole invoice body is gone. The observed failure calls for a review/fallback path for missing invoice-body evidence.
2. **Block coverage is misleading if read as extraction accuracy.** Each OCR page becomes one block. Linking any part of that block marks the whole block mapped; both results report 100%. It proves neither full text-span assignment nor capture of the source image. Keep this metric explicitly separate from completeness.
3. **Table/row reconstruction depends on line breaks.** On scan_023, one flattened OCR line becomes one semantic group and loses all billed/tax rows. The actual canonical readings advertise `tables=false`, `layout=false`, `block_kinds=false`; coordinates and confidence are absent. Table/layout accuracy therefore cannot be measured from this reader's output.
4. **Schema validity and provenance links are insufficient acceptance tests.** Null main fields and empty arrays are schema-valid. `nonempty_interpretation` counts annotations (and a non-unknown document type), so it does not require usable invoice facts. Evidence links point to the OCR output; they cannot detect an upstream OCR omission. Missing essentials should cause review without inventing their values.
5. **Identifier normalization is too weak for this OCR output.** The inserted spaces in the order and customer tax ID survived. Any repair should retain literal OCR evidence and use explicit validation rules; fuzzy rewriting of bank/tax identifiers would create a different risk.
6. **Source uncertainty and non-text features are not surfaced.** Both readings have empty uncertainty lists and no non-text elements. The shadows, blur, mirrored content and stamps visible to a reviewer are absent from those signals. Background text must remain separate from the foreground invoice if a stronger reader recovers it.

Priorities: first prevent the false `completed` result; then evaluate an OCR/layout fallback on separate development examples; then restore line/tax grouping and identifier validation. Freeze those changes before evaluating on new held-out cases. These two documents have now been inspected and should not be called untouched holdouts in future tuning.

## OCR metrics and limitations

Whitespace-normalized character error rate is **35.14%** for scan_023 and **70.08%** for scan_025. Exact numeric/identifier-token recall is **4/11 (36.36%)** and **0/17 (0%)**, respectively. Literal token matching penalizes decimal punctuation and internal spaces even where amounts are recognizable. It is not semantic amount accuracy.

Uncertain reference blocks are excluded wholesale: scan_023's obscured identifier/location block, bank-account block, faint footer and mirrored-content block; scan_025's mirrored-content block. Predictions cannot always be aligned to excluded regions. The JSON report preserves the exact excluded IDs and field pointers. These exclusions and the draft references limit how strongly text-error metrics can be interpreted.

## Evidence and reproduction

- [Machine-readable scores, actual OCR and invoice JSON](../benchmark/reports/product-hard-two-20260919.json)
- [Two-file input selection](../benchmark/manifest-hard-two-20260919.json)
- [Offline audit script](../benchmark/score_hard_two_20260919.py)
- Local private evidence: `benchmark/runs/hard-two-20260919/` contains source renders, timestamped events, wall-clock measurement, source/code hashes, immutable export, raw provider requests/responses, source-linked interpretation evidence and test logs. This directory is intentionally Git-ignored. Persisted evidence also remains under the batch ID above.

From the repository root, rescore the saved export without provider calls:

```sh
benchmark/.venv/bin/python benchmark/score_hard_two_20260919.py
```

A **new paid live run** of the same product inputs uses:

```sh
.venv/bin/python -m ingestion.cli ingest \
  --manifest benchmark/manifest-hard-two-20260919.json \
  --interpreter jev --concurrency 2
```

Export the new batch into a new directory; do not overwrite this audit's evidence or label a new run with the old batch identity. The score script deliberately targets this saved audit snapshot.

Verification: **43 product tests passed** and **34 benchmark tests passed** in their respective existing virtual environments. An initial combined invocation used the product environment for benchmark tests and failed collection because its optional PyMuPDF/SciPy dependencies are not installed there; the separate benchmark environment passed. These tests verify implementation mechanics, not extraction quality. No production fix was made during this benchmark.

## Field-by-field audit

The following tables show every scored, non-null core reference field. Annotations and explicitly uncertain fields are excluded here; the JSON contains the full scoring details. Missing collection rows are shown as null predictions. Amounts compare with Decimal; names and identifiers are not fuzzily matched.

### scan_023.pdf

| Field | Expected | Actual | Result |
|---|---|---|---|
| /document_type | invoice | invoice | correct |
| /invoice_number | 2026/22608 | 2026/22608 | correct |
| /issue_date | 2026-04-29 | 2026-04-29 | correct |
| /purchase_order_reference | PO-2026-0726 | PO- 2026-0726 | incorrect |
| /currency | EUR | EUR | correct |
| /supplier/name | Catering Hermanos Pico S.L. | Catering Hermanos PicoS. L. | incorrect |
| /supplier/location | Valencia | null | missing |
| /customer/name | Banco Miralmar S.A. | null | missing |
| /customer/tax_id | A58231074 | A 58231074 | incorrect |
| /totals/taxable_base | 2050.00 | null | missing |
| /totals/total | 2480.50 | 2480.5 | correct |
| /lines/0/description | Servicio mensual | null | missing |
| /lines/0/amount | 683.33 | null | missing |
| /lines/1/description | Mantenimiento trimestral | null | missing |
| /lines/1/amount | 683.33 | null | missing |
| /lines/2/description | Suministro pedido | null | missing |
| /lines/2/amount | 683.34 | null | missing |
| /taxes/0/label | IVA | null | missing |
| /taxes/0/rate_percent | 21 | null | missing |
| /taxes/0/amount | 430.50 | null | missing |

### scan_025.pdf

| Field | Expected | Actual | Result |
|---|---|---|---|
| /document_type | invoice | other | incorrect |
| /invoice_number | 2026/27401 | null | missing |
| /issue_date | 2026-03-22 | null | missing |
| /purchase_order_reference | PO-2026-0728 | null | missing |
| /currency | EUR | null | missing |
| /supplier/name | Limpiezas Turia S.L. | null | missing |
| /supplier/tax_id | B98120774 | null | missing |
| /supplier/location | Valencia | null | missing |
| /customer/name | Banco Miralmar S.A. | null | missing |
| /customer/tax_id | A58231074 | null | missing |
| /payment/iban | ES4414650100951704302211 | null | missing |
| /totals/taxable_base | 640.00 | null | missing |
| /totals/total | 774.40 | null | missing |
| /lines/0/description | Servicio mensual | null | missing |
| /lines/0/amount | 320.00 | null | missing |
| /lines/1/description | Mantenimiento trimestral | null | missing |
| /lines/1/amount | 320.00 | null | missing |
| /taxes/0/label | IVA | null | missing |
| /taxes/0/rate_percent | 21 | null | missing |
| /taxes/0/amount | 134.40 | null | missing |
