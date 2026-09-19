# Hard-two repair audit — 19 September 2026

The replacement reader substantially improves foreground extraction, but automatic extraction is not yet reliable enough to claim the two invoices are fully correct or that the approach generalizes. No invoice-specific facts or filenames were found in production extraction code. That alone does not establish generalization: the prompts and crop strategy were developed using these documents.

| Run | scan_023 core facts | scan_025 core facts | Review assistance |
| --- | --- | --- | --- |
| Original GOT OCR + Jev | 5/20 | 0/20 | None |
| First region vision + DeepSeek | 19/20 | 20/20 | None |
| Fresh repeat, same current reader | 20/20 | 19/20 | None |
| Explicitly reviewed reading + DeepSeek | 20/20 | 20/20 | AI visual review corrections |

Scores normalize formatting and exclude uncertain draft-reference fields and annotations. They are not whole-document accuracy. Both new automatic runs preserve all five foreground billed rows and both tax rows. The repeat batch is `24542783-1f09-4a56-81a1-3d1eae7e67f1`; both documents were marked `needs_review`, with zero provider failures.

## What remains wrong in the fresh automatic repeat

- scan_023: supplier tax ID became `B9623341B`, without an uncertainty flag. This field is excluded from the draft-reference core score, so 20/20 hides this problem. Earlier readers disagreed on the final glyph; the reviewed candidate ends in 9 and remains uncertain.
- scan_023: handwritten `OK` became `OK A`; this was flagged ambiguous. The blurry footer was accent-normalized and lost punctuation, without uncertainty. Background transcription still contains unverifiable names, dates and amounts, although those remain separated from foreground invoice fields.
- scan_025: the reader appended `[unreadable]` after the complete foreground IBAN. The interpreter then set the IBAN to null and preserved it only as a candidate. Background bank text was truncated and the background customer name was misread.

The concrete next reliability problem is visual verification and uncertainty calibration: correct readable foreground values must survive nearby noise, and uncertain characters must not become confident facts. A second image check must be evaluated against these failures, without invoice-specific substitutions. Background source membership must remain separate from the foreground invoice.

## Deliverables and reproducibility

Reviewed invoice JSONs, evidence, validation and source-review provenance are in `output/verified-invoices/`. They are explicitly labeled review-assisted. The original automatic and reviewed exports remain immutable under `benchmark/runs/repair-hard-two/`.

Hash-verified audit reports:

- `benchmark/reports/hard-two-automatic-first-audit.json`
- `benchmark/reports/hard-two-automatic-repeat-audit.json`
- `benchmark/reports/hard-two-reviewed-audit.json`

Reproduce a report without provider calls:

```sh
benchmark/.venv/bin/python -m benchmark.audit_bundle \
  --export benchmark/runs/repair-hard-two/export-automatic-repeat \
  --selection benchmark/runs/hard-two-20260919/selection.json \
  --output benchmark/reports/hard-two-automatic-repeat-audit.json
```

Current application tests: 58 passed. These tests check contracts and pipeline behavior, not visual OCR accuracy. No new provider, paid plan or credits were added. Exact call costs are not provided by the existing endpoint.

These two invoices are now development/regression examples. An untouched set across comparable and easier difficulty is required before making broader reliability claims. Completion remains unproven: reviewed structured outputs exist, but automatic all-detail correctness and generalization do not.

## Automatic version 2: independent reading and bounded comparison

Batch `72730e24-dddc-4f49-8f75-26a084ef6a73` scores **20/20 + 20/20 core facts**, without explicit review corrections. All five billed rows and both tax rows match. Both outcomes remain `needs_review`. Reading took 88.54 and 89.45 seconds, compared with roughly 30 seconds for the prior single-reader approach. Exact costs remain unknown.

The new reader obtains a second independent transcription before comparing both drafts with the original region images. Its comparison contract requires one response for every original block, preventing silent omissions during this step. All calls and image hashes remain auditable. Unit suite: 59 passed; focused lint passed.

This fixes the observed foreground IBAN omission and yields the reviewed tax-ID candidate, but does not establish all-detail correctness: the smudged tax-ID still lacks an automatic uncertainty flag; the small handwritten annotation and foreground footer are not fully resolved; background transcription remains unreliable. The 40/40 core result explicitly excludes those uncertain reference fields and annotations.

Friendly automatic JSONs are in `output/automatic-invoices/`, separate from review-assisted outputs. Hash-verified results are in `benchmark/reports/hard-two-verified-automatic-audit.json`.

A broader check was selected before inspecting new source/reference values for this experiment: the first held-out entry in each original filename group (`2026-01-16_P004.pdf`, `FA-4816_seguridad.pdf`, `scan_021.pdf`). Selection and code hashes are recorded under `benchmark/runs/verification-generalization/`. This small, deterministic sample is not a statistical reliability estimate.

## Frozen-version broader check

Batch `5ba80b2c-983a-4b81-a685-7e0fd83ab6c0`, with unchanged extraction code, scored:

| Invoice | Core facts | Runtime result | Finding |
| --- | --- | --- | --- |
| 2026-01-16_P004.pdf | 20/21 | completed | Madrid preserved in full address, but missing from customer.location |
| FA-4816_seguridad.pdf | 20/20 | completed | Different clean tabular layout; quantities, both rows and tax correct |
| scan_021.pdf | 18/18 | needs_review | Heavy horizontal interference; hidden invoice number and supplier tax ID left null |

This is **58/59 scored readable core facts**. The clean invoice marked completed despite a missing structured location is a demonstrated acceptance limitation. The noisy scan needed one HTTP-error retry; its hidden fields are excluded from the scored denominator, not recovered correctly by magic. Its faint footer is still not character-exact. All five billed rows and three tax rows across this sample were recovered.

Report: `benchmark/reports/vision-verification-generalization.json`. Original selection and code hashes predate source inspection for this experiment. Two clean layouts and one severely degraded scan give useful evidence beyond the original pair, but three documents cannot establish general corpus reliability.

After this evaluation, the interpretation prompt was updated generically to populate a party's location when the printed address explicitly identifies a city, while retaining the full address. No city or invoice-specific value was added. Subsequent retests of these same readings are regression checks, not fresh held-out validation.

### Structure-prompt regression results

Reinterpreting the same saved readings with the city-mapping instruction kept the original pair at 40/40 core facts (batch `c3900d23-da27-4f49-a756-4185723c66fa`). On the three additional invoices, batch `b956280f-9772-4556-b6a5-529f77161216` recovered the missing city but scored 57/59: the clean invoice returned currency `€` instead of `EUR` and shortened `Valencia · España` to `Valencia`. These are real regressions; the better earlier scores are not substituted for them. Reports: `hard-two-structure-regression.json` and `vision-structure-regression.json`.

The next prompt revision explicitly preserves standalone location strings including country/region and uses ISO currency codes for printed currency symbols. It introduces no supplier, filename, amount, or invoice-specific correction. Its subsequent test is another regression test on saved automatic readings.

### Contract clarification regression result

Batch `a6037b18-bf5c-4436-adba-c0e585fc9d85` matches **59/59 scored core facts** across the three saved automatic readings: 21/21, 20/20, and 18/18. It recovers the explicit customer city while retaining the complete supplier location and normalized EUR code. Report: `benchmark/reports/vision-contract-regression.json`.

This is improvement after tuning on observed failures, not a replacement for the frozen-version 58/59 result. A deterministic validator now rejects currency symbols in the currency-code field; it preserves the model's value and requests review instead of silently changing it. All 59 application tests pass after that guard, including the observed symbol regression. The validator checks code shape, not membership in the entire ISO catalogue.

Measured provider-stage spans (exclude initial source upload and post-run export): original pair version-2 live run **150.51 seconds** from first reading attempt start to last interpretation completion. The three-document broader check took **346.70 seconds**, including the striped scan's failed reading attempt and retry. These are observed small-run timings, not throughput guarantees. Currency costs remain unknown, not zero.

## Final same-contract regression check

Batch `530718ca-f59b-4fa5-a8fb-7d8d63e59842` reinterprets the original pair's unchanged automatic version-2 readings with the final `alpha-4` contract instructions. It again matches **40/40 scored core facts**, all five billed rows and both tax rows. The same interpretation version matches 59/59 on the additional three saved readings. Report: `benchmark/reports/hard-two-contract-regression.json`.

The friendly JSONs in `output/automatic-invoices/` now point to this final interpretation batch. No manual reading corrections were used. scan_023 remains `needs_review`; scan_025 is marked `completed`, but its background annotation still includes an incomplete bank account and misread customer name. This is a remaining limitation of status validation: it does not prove character-level correctness of all annotations. The unflagged ambiguity in scan_023's tax digit and handwritten note also remains. Core-score success must not be reported as full-goal completion.

Completion audit: structured JSON delivery, line/tax reconstruction, evidence links, use of existing providers, and general-purpose implementation are demonstrated. Automatic all-detail correctness and reliable uncertainty detection are not yet demonstrated. Some original pixels are genuinely ambiguous; no clearer source has been supplied. Broader evidence remains the original frozen 58/59 result plus explicitly labeled development regressions, not a guarantee about every invoice of similar difficulty.

## Native-source fidelity audit and completion blocker

Direct PDFium inspection confirms that each PDF has one page with one image object and no embedded text. Native decoded image dimensions are **496 × 701** for scan_023 and **1654 × 2338** for scan_025. The 300-DPI renders enlarge those existing pixels; they do not reveal an additional higher-resolution page image. Machine-readable evidence and source hashes: `benchmark/reports/source-fidelity-audit.json`.

Visual inspection of the native scan_023 image still cannot independently establish the smudged tax-ID digit or the tiny foreground footer character by character. This same source ambiguity has persisted through the initial review, repeated automatic trials, independent reading/comparison, and the native-source audit. Exact all-detail verification is blocked pending a clearer original or a trusted transcription of those details. Model agreement or repeated calls cannot substitute for source evidence. This does not erase the remaining automatic uncertainty-detection and background-transcription limitations described above, nor does it change the objective to core fields only.
