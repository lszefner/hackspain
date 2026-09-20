# Caja invoice correction — 20 September 2026

## Scope and agreed policy

Correct the same 500 stored submissions after the interrupted
`caja498-20260919T234346Z` run, preserving original PDFs, extraction artifacts,
and prior evaluations. Contextual AI review remains disabled. Verdicts are
recommendations and do not execute or authorize payments.

The owner confirmed:

- Match supplier NIF and invoice IBAN against the master; do not require NIF
  checksum validation for this Caja dataset.
- Require currency explicitly evidenced on the invoice. Missing currency
  escalates; never silently supply EUR.
- The order sheet/ERP omit currency. The frozen `order_currency_policy: invoice`
  compares an unlabelled order total in the explicitly evidenced invoice EUR
  denomination. It does not manufacture an `order.currency` fact. An explicit
  different, ambiguous, or invalid order currency still blocks comparison.
- Treat this operation as correction of the same submissions, not another
  submission of each invoice. Other files still participate in duplicate checks.

The balanced profile also follows the written Norma_Pagos_v3 date requirement:
valid and non-future, without an additional supplier-payment-window age limit.
This rerun preserves the earlier explicit `issue-date` evaluation-date policy.
Consequently it is not an assessment against today's date.

## Findings

The live API initially reported 318 evaluated invoices, all ESCALAR, and 182
errors after interruption. Of the evaluated records, 316 had unavailable ERP
state/history coverage blockers, 314 had unavailable order currency, and 290
had NIF checksum failures. These counts overlap.

The ERP had been unavailable when the original batch froze its source snapshot.
Restarting it cannot change that immutable snapshot; a fresh evaluation is
required. The corrected run captures all 516 ERP records across 26 pages before
evaluating anything and refuses to continue with an incomplete ERP snapshot.

The supplier master has 11 suppliers: 10 NIFs fail the checksum. Of the reference's
261 approvals, 243 have an extracted NIF that both fails the checksum and exactly
matches the master. Thus retaining checksum validation would materially change
the reference's policy, even without extraction errors.

There are 326 explicitly extracted EUR currencies and 174 missing currencies.
The stored readings for the latter contain no detected currency code, currency
symbol, or euro/dollar word. Missing currency is preserved as an escalation.

Incomplete history rows previously blocked unrelated invoices. The execution
adapter now distinguishes a possible duplicate from a row ruled out by known,
unequal key components. Unknown possible matches remain blocked; confirmed
duplicate submissions still reject without being labelled paid.

One extraction, `scan_028.pdf`, had an unknown interpretation outcome after a
response-persistence error. Its successful reading was reused for one explicit
interpretation retry; the new call returned HTTP 200 and stored a `needs_review`
outcome without an operational error. Provider cost was not reported and is
not claimed to be zero. All other extraction outcomes were reused.

The dashboard API on port 8010 was running from `feat-ui-improvements`, while
this work was in `fix-save-invoices-without-deepseek`. Its invoice-detail request
disconnected. The idle API was restarted from this checkout; detail and flow
reads work with the current backend.

## Implementation

- `backend.correct_revision` adds an explicit, durable correction intent. It
  verifies local PDF hashes against stored inputs, freezes sources and policy,
  appends evaluations to existing input IDs, retains previous records, and emits
  actual completed counts. Up to three evaluations run concurrently. It performs
  no extraction/provider calls. An interrupted correction remains unknown and
  needs a new explicit intent; an existing intent is never automatically repeated.
- Correction records link the API projection to their own run/policy instead of
  presenting the old interrupted run as the source of the new decision.
- History projections use verified Postgres audit JSON in bounded groups of 64
  instead of repeated Storage reads. Original/source byte verification remains
  part of engine evaluation.
- Raw provider responses bypass canonical-JSON addressing and remain verbatim
  private Storage artifacts, including empty/non-JSON error bodies.
- Targeted retry selection can restrict a new attempt to one input.
- Existing `evaluation-result/1` schema and historical artifacts are unchanged.

The corrected frozen rules are derived from the original rules with an explicit
amendment artifact, not regenerated to fit reference labels. The original rule
sources, old rules, corrected profile, and amendment are included in lineage.

## Reference comparison

The reference files contain only `file_id` and `result`, not reasons or source
traces. They are a comparison baseline, not proof of why any individual decision
is correct. Neither input file was modified.

| File | Rows | PAGAR | ESCALAR | NO_PAGAR |
|---|---:|---:|---:|---:|
| Downloads/outcomes.jsonl | 500 | 261 | 230 | 9 |
| Downloads/outcomes_lote2.jsonl | 40 | 13 | 27 | 0 |
| Corrected 500-file preview | 500 | 261 | 230 | 9 |

The preview matches 496/500 individual reference verdicts (99.2% agreement).
This is agreement with the supplied output, not measured ground-truth accuracy.
The 40-file second lot was inspected as a reference but is outside this 500-file
rerun.

| Invoice | Reference | Corrected | Evidence/reason |
|---|---|---|---|
| scan_006.pdf | ESCALAR | PAGAR | PDF visibly states EUR; configured arithmetic, identity and ERP checks pass. |
| scan_010.pdf | ESCALAR | PAGAR | PDF visibly states EUR; configured arithmetic, identity and ERP checks pass. |
| scan_008.pdf | PAGAR | ESCALAR | Stored extraction has unlinked/invalid evidence and an annotation describing a scan smudge. Review is disabled; unresolved evidence is not overridden. |
| scan_011.pdf | PAGAR | ESCALAR | Stored extraction flags IBAN evidence as ambiguous. The visible label appears readable, but no replacement verified extraction was fabricated. |

All four PDFs were rendered and inspected. Their reference labels were not used
to overwrite the extraction or evaluator decision.

Reference SHA-256:

```
outcomes.jsonl        bb37947d2b9ed2164cefb3edaad9619d3a085fd92d9c78831869fd6021eeac98
outcomes_lote2.jsonl  953c6c7b825a103f156497757ba0894d03599555bb4dc4270f16168002419390
```

## Verification and artifacts

Offline checks use disposable local Postgres and mocked storage/providers.
207 core, tax, execution, and API tests passed; 265 contextual-review and
decision-context tests passed in a separate overlapping suite; 13 export tests
passed. Subsequent correction-concurrency and history-projection checks passed.
These counts overlap and are not additive. Live operations are reported above
separately from tests.

Private run artifacts are under `backend/data/correction-20260920/` (gitignored):
frozen rules/sources, policy amendment, preview, per-file comparison, correction
request key, persistence log, and final result. The sequential correction was
interrupted deliberately to switch to bounded concurrency; its completed audit
records remain intact.

Live persistence run: `caja500-correction-20260920T004756Z`.
Completed: all 500 invoices were evaluated and persisted, with the final invoice
completed after 449.2 seconds. Final API verification reports 500 completed and
zero operational errors. All 500 latest results link to this correction and
retain the disabled contextual-review policy. Sample flow endpoints for each
decision category also match the stored results.

Final decisions: **261 PAGAR, 230 ESCALAR, 9 NO_PAGAR**. All 174 invoices missing
currency remain escalated; no PAGAR result lacks explicit EUR evidence. The
reference agrees on 496 of 500 individual decisions; the four differences are
documented above. This is reference agreement, not a measured accuracy claim.

The verified 500-row deliverable is
`backend/data/correction-20260920/outcomes.jsonl`; machine-readable verification
is `backend/data/correction-20260920/verification.json`. ERP on port 8009 and the
dashboard API on port 8010 were running at final verification.
