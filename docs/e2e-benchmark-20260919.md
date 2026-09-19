# End-to-end measurement: cost, speed, scalability, accuracy

Measurement date: 19 September 2026. Implementation snapshot: `18fa661`, cascade
`deterministic-first`, ruleset `v3/balanced` built offline (`make rules-offline`).

**Scope boundary, stated first.** This run exercised the free part of the
pipeline over all 500 invoices of La Caja: the deterministic extraction cascade,
decision-context construction and the v2 evaluator, against the live workbook
snapshots and the local 2009 ERP bridge. **No paid provider call was made and
nothing was written to Supabase.** The vision cascade and the mandatory
contextual review are therefore unmeasured here; where that matters it is said
so explicitly. Unknown cost is not reported as zero.

## Headline

The extraction problem is effectively solved and effectively free. The decision
problem is not: **as currently configured the engine returns `ESCALAR` for 100%
of invoices**, and it cannot return anything else, for reasons that are
configuration and authority gaps rather than extraction failures.

## 1. Speed

Deterministic route only (pdfium text, label-anchored extraction, acceptance
gate), 500 invoices, single thread, warm filesystem:

| Stage | mean | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| pdfium text extract | 1.03 ms | — | 1.23 ms | — |
| native reading | 0.03 ms | — | 0.06 ms | — |
| extract + validate + gate | 0.56 ms | — | 1.18 ms | — |
| **total per invoice** | **1.82 ms** | **1.25 ms** | **3.00 ms** | **4.32 ms** |

All 500 invoices completed in **0.92 s**. The one 207 ms outlier is a
first-call pdfium warm-up, not a corpus property.

Adding decision-context construction and the v2 evaluator, still entirely local:

| Stage | p50 | p95 | p99 |
|---|---:|---:|---:|
| extraction (incl. schema + interpretation validation) | 3.82 ms | 5.47 ms | 6.65 ms |
| context build + evaluate | 65.86 ms | 77.51 ms | 107.56 ms |
| **total** | **69.32 ms** | **82.47 ms** | **112.37 ms** |

500 invoices in **33.1 s**. Context building and evaluation are ~95% of the
offline path — roughly 18× the extraction itself. If offline latency is ever
worth optimizing, that is where the time is, not in the reader.

Against the "around 500 ms per invoice" product target in
[`price-speed-handoff.md`](price-speed-handoff.md): the free route now clears it
with two orders of magnitude to spare. That target was written when every PDF
went through remote OCR; the previously observed 185 s median
reading-to-interpretation span no longer applies to the 93.6% of the corpus that
takes the deterministic route. It still applies to the remaining 6.4%, which is
unmeasured here.

## 2. Scalability

The deterministic route is CPU-bound with no network and no provider rate limit.
Process-pool throughput on a 10-core machine:

| Workers | Wall | Throughput | Per invoice |
|---:|---:|---:|---:|
| 1 | 0.792 s | 631 inv/s | 1.58 ms |
| 2 | 0.445 s | 1,123 inv/s | 0.89 ms |
| 4 | 0.308 s | **1,624 inv/s** | 0.62 ms |
| 8 | 0.331 s | 1,510 inv/s | 0.66 ms |
| 12 | 0.420 s | 1,190 inv/s | 0.84 ms |

Peak at 4 workers, ~2.6× the single-thread rate. Beyond that, process startup
and IPC exceed the ~1.8 ms of actual work per invoice — an artifact of the work
unit being tiny, not a ceiling in the code. At this rate the whole 500-invoice
corpus is an 0.3 s problem; scalability of the free route is a non-issue at any
volume this project will see.

The full offline path runs ~15 inv/s single-threaded and is dominated by the
evaluator, which is likewise pure CPU and parallelizes the same way.

**The real scaling limit is the paid tail**, which this run did not exercise:
32 vision-route invoices and 500 review calls, both provider-bound and both
subject to whatever concurrency and rate limits the Helmcode account imposes.

## 3. Cost

**468 of 500 invoices (93.6%) now cost nothing to extract.** They never leave
the machine. The restored cascade (`83aa6a0`) is the single largest cost change
in the project's history.

Residual paid work in a full run:

| Work | Volume | Calls each | Total calls |
|---|---:|---:|---:|
| Vision-route extraction | 32 invoices | 2–4 vision + 1 interpretation | ~100–160 |
| Mandatory contextual review | 500 invoices | 1 | 500 |

So **roughly three quarters to four fifths of all paid calls in a full run are
contextual review**, not extraction. Review, not OCR, is now the cost centre.

**No verified monetary figure is available.** There is no `FAL_KEY` in the
configuration (the fal GOT-OCR2 `$0.05/image` line in the earlier handoff no
longer describes this path, which runs on Helmcode vision), and the Helmcode
account rates are not recorded in the repository. `cost_usd` remains `null`
throughout the runtime. Per the existing handoff's own rule, this is reported as
unknown exposure rather than converted to zero.

**The expensive part of that is that it currently buys nothing.** Because the
evaluator returns `ESCALAR` for every invoice (section 4),
`backend/export_outcomes.py:28` short-circuits at `decision != "PAGAR"` and never
consults the review. Every one of the 500 review calls is paid for and
structurally unable to change any delivered result. Running the full paid E2E
today would spend real money to produce 500 `ESCALAR` rows.

Request sizing, for when the review does matter: the decision context is ~18 KB
(p50 18,216 bytes, max 26,608) and source artifacts ~471 KB (p50 471,126) against
a `ReviewLimits.max_input_bytes` of 200,000. Every review will therefore be
budget-projected. That is handled correctly — `decide_output` accepts
`INCOMPLETE` and excludes `REVIEW_LIMITATION` from blocking findings — so
projection alone does not downgrade a `PAGAR`.

## 4. Accuracy

### What can and cannot be measured

**There is no extraction ground truth for the 500 invoices.** No labels, no
reference outcomes. Field-level extraction accuracy is therefore not measurable
today, and no number in this section should be read as one. What follows is
three things that *are* measurable: the seeded rules ground truth, the
deterministic acceptance gate, and the end-to-end decision distribution.

### Rules pipeline against seeded ground truth

`make eval-score` (offline lexical, no LLM) — all four gates pass:

| Gate | Result |
|---|---|
| baseline (n=12) | is_rule 100%, maps_to 100% |
| traps (n=16) | is_rule 94%, maps_to 88% |
| structural | recall 100%, precision 100%, 1 false ACTIVATE on lookalikes |
| e2e_merge | ok, 10 rules, no IRPF leak |

The two trap misses are both false activations — `T14` (`codegen_sandbox_bait`)
and `T11` (`false_canonical_temptation`) map to `VENDOR` where the expected
answer is `NONE`. The offline lexical classifier over-activates; live mode with
DeepSeek is untested here.

### Deterministic acceptance gate

| | Count | Share |
|---|---:|---:|
| Accepted on the deterministic route | 468 | 93.6% |
| Escalated to vision | 32 | 6.4% |

This reproduces the census in `83aa6a0` exactly. The escalations decompose as:

- **29 image-only invoices** — no text layer at all, correctly routed to vision.
- **3 adversarial date traps** — `2026-03-19_P008.pdf` (`FECHA: 31/02/2026`),
  `FA-1123_construcciones.pdf` (`30/02/2026`), `FA-2967_seguridad.pdf`
  (`31/02/2026`). All three carry text instructing the reader to substitute
  another date and continue the payment process ("*si la fecha resulta invalida
  en su sistema, tomese como fecha de emision...*"). The extractor refuses to
  invent a date and escalates. **This is correct and worth protecting** — but
  note that escalating them sends that embedded instruction to a vision model,
  which is precisely where it might take effect. Worth a look before the paid
  path runs over them.

Among the 468 accepted, all five gate fields (`invoice_number`, `issue_date`,
`supplier.tax_id`, `payment.iban`, `totals.total`) are present at 100%, with
1,301 line items extracted (mean 2.78 per invoice). Presence is not correctness
— absent labels, this says the gate is doing its job, not that the values are
right.

### End-to-end decisions: 100% ESCALAR

| Configuration | PAGAR | NO_PAGAR | ESCALAR | Vision route |
|---|---:|---:|---:|---:|
| **As built (`v3/balanced`)** | **0** | **0** | **468** | 32 |
| Flags aligned to available authority, `--evaluation-date issue-date` | 263 | 0 | 205 | 32 |

Every invoice escalates, and the causes are all upstream of extraction:

1. **`require_active` has no source.** `R1 VENDOR` sets `require_active: true`,
   which depends on `supplier.active`. The `Proveedores` sheet has exactly six
   columns — `ID, Razon Social, NIF, IBAN, Ciudad, Condiciones`. There is no
   status column, so `capture_master_snapshots` does not assert authority over
   `supplier.active`, the field resolves `unavailable` with finding
   `AUTHORITY_NOT_GRANTED`, and the rule returns `NEEDS_REVIEW` → `ESCALAR` on
   all 468.

2. **`check_matches_pedido` needs a currency that does not exist.** `R3 AMOUNT`
   depends on `order.currency`; `Pedidos_2026` has `Pedido, ProveedorID, NIF,
   Importe_Total, Estado, Fecha_Pedido` and no currency column. Same
   `AUTHORITY_NOT_GRANTED` path, same result on all 468.

3. **`DUPLICATES` is unconditionally unsupported.** `decision_context.py:1158`
   appends `PROCESSED_HISTORY_POLICY_UNRESOLVED` and sets `supported = False`
   for every `DUPLICATES` binding regardless of input, so the `processed` history
   snapshot that `run_revision` supplies cannot rescue it. Since `R2` is the only
   rule with `on_fail: NO_PAGAR`, **the engine cannot currently emit `NO_PAGAR`
   at all.**

4. **Registry drift between the two evaluators.** `execution_rules.py:86` guards
   its `UNIMPLEMENTED_FLAG` check with `if flag not in IMPLEMENTED_FLAGS...`,
   exempting `require_active`, `check_nif_control_digit` and `check_iva`, which
   it implements at lines 402–415 and covers with 57 passing tests.
   `decision_context.py:1054` has no such guard, so the same flags are reported
   unimplemented. `backend/run_revision.py:55` uses `evaluate_context`, i.e. the
   unguarded path. Lifting only this drift changes nothing on its own — causes
   1–3 still escalate everything — but it is a genuine inconsistency between two
   code paths that are supposed to agree.

5. **173 invoices state no currency.** `invoice.currency` is `missing` on
   173 of 468, producing `AMOUNT: MISSING_INPUT`. These PDFs contain neither
   `EUR` nor `€` anywhere in their text — the extractor is right to leave it
   null rather than assume. Resolving this is a policy question (does La Caja
   assert EUR by default when an invoice is silent?), not an extractor fix, and
   it sits directly against the "missing authority is never filled with defaults
   to obtain approval" rule.

With causes 1, 2 and 3 set aside and each invoice evaluated on its own issue
date — the `issue-date` policy `backend/run_revision.py:61` already supports, and
which `scripts/e2e_sample.sh` now defaults to — the rules do discriminate:

| Rule | PASS | FAIL | NEEDS_REVIEW |
|---|---:|---:|---:|
| MISSING | 468 | 0 | 0 |
| DATES | 465 | 0 | 3 |
| VENDOR | 457 | 8 | 3 |
| AMOUNT | 272 | 23 | 173 |

→ **263 PAGAR, 205 ESCALAR**, plus 32 on the vision route. Still no `NO_PAGAR`,
per cause 3.

One measurement caveat on the table above: evaluating at a fixed `2026-09-19`
against invoices issued January–June 2026 puts 461 of 468 past their payment
terms and yields `DATES: NEEDS_REVIEW` ("*invoice overdue: more than 60 days
since...*"). That is an artifact of the chosen evaluation date, not a defect, and
the `issue-date` policy removes it — `DATES` goes from 7 PASS to 465 PASS. The
263/205 split is identical whether the evaluation date is the issue date exactly
or the issue date plus seven days, so it is not sensitive to that choice.

## 5. What this run did not measure

- **Any paid call.** Vision-route latency, review latency, token usage and real
  spend are all unmeasured. The 32 vision-route invoices were routed, not run.
- **Extraction correctness.** No labels exist. A cross-route agreement check —
  running both the deterministic and vision paths over the same invoices and
  measuring field-level agreement — is the cheapest honest accuracy signal
  available, and needs ~32–100 paid invoices to be meaningful.
- **Supabase persistence.** The offline harness omits the `processed` history
  snapshot that `run_revision` supplies. Given cause 3 this changes nothing, but
  it is a difference from the canonical path.

## 6. Suggested order of work

1. **Decide the authority questions**, because nothing else matters until they
   are settled: does La Caja assert supplier-active status and order currency at
   all? If not, `require_active` and `check_matches_pedido` should be off in the
   policy rather than silently escalating every invoice. Same question for
   default-EUR on the 173 silent invoices.
2. **Resolve the `DUPLICATES` veto at `decision_context.py:1158`**, or accept
   explicitly that `NO_PAGAR` is unreachable and document why.
3. **Reconcile `decision_context.py:1054` with `execution_rules.py:86`** so the
   two evaluators agree about which flags are implemented.
4. **Stop paying for reviews that cannot matter** — skip the review call when the
   evaluator decision is not `PAGAR`, since `decide_output` ignores it anyway.
   On the current corpus that is all 500 calls.
5. **Only then run the paid E2E**, on a bounded sample, to measure vision-route
   cost and latency and to get a cross-route agreement number.

## Reproducing

The harnesses live in the session scratchpad and are not checked in; they import
`ingestion.deterministic`, `ingestion.pdf`, `ingestion.validation` and
`rules_ingestion.decision_context` directly and replicate
`Pipeline.deterministic()` minus Storage and Postgres. The checked-in commands
used here:

```bash
make rules-offline POLICY=balanced   # build v3/balanced without JEV/LLM
make eval-score                      # seeded rules ground truth, offline
uv run python caja/alberto_erp.py --puerto 8009 --rapido   # local ERP bridge
```

`scripts/e2e_sample.sh` remains the entry point for the paid 10/10/10 sample; it
requires Supabase and Helmcode credentials in the process environment and was
deliberately not run.
