# Invoice ingestion benchmark alpha

**50 draft reading references and 50 draft invoice references. Zero human-verified
references.** Every selected page was rendered and visually inspected by Codex.
Results against these annotations are provisional. This benchmark does not cover
payment decisions or establish performance on all 500 invoices.

**Product evaluation:** score the actual `ingestion` pipeline's exported predictions
using the import-output workflow below. The provider adapters in this directory
are separate experimental extraction implementations; their saved scores are not
product scores. In particular, the historical Jev development baseline does not
measure the product's `jev-choice-0.2` implementation. Reference answers stay fixed
when evaluating a new product version. See the
[product operator guide](../docs/ingestion-agent-operator.md#product-jev-extraction-version-jev-choice-02).

## Selection and reference independence

The user authorized first 10, middle 20, last 20. `manifest.json` freezes Unicode
filename-ascending order across the 500 PDFs: slices `[0:10]`, `[240:260]`,
`[480:500]`. Each entry has exact filename, relative path, SHA-256 and split.
First 6/12/12 of those groups are development (30); remaining 4/8/8 held out (20).
No filename is content evidence. All 50 PDFs have one page; 20 are scans. This
positional sample is not random or a proven representative corpus sample.

Codex inspected all complete rendered pages and enlarged difficult regions.
Embedded text assisted transcription of 30 digital PDFs. Twenty scans were
transcribed from page images without OCR, Jev or DeepSeek assistance. No evaluated
provider generated or corrected either reference. Annotation-only assembly inputs
are under `review/annotation-source/`; their assembler is separate from runtime
adapters. **Do not use that assembler as an extraction baseline.**

Important reference decisions:

- Literal stage 2 text retains numbers, identifiers, spelling, dotted leaders,
  notes and small print. Table text occurs only in cells, avoiding duplication.
  Visible table rules/background/stamp outlines have separate descriptions.
- The invisible embedded paragraph in `FA-5044_mensajería2.pdf` is excluded from
  visual text, with a review note. The visible instruction-like note in
  `FA-4290_mensajería.pdf` remains document data. Neither drives actions.
- Zero-width embedded characters inside one total are not visible glyphs.
- U+FFFD (`�`) in damaged scans marks an explicitly unreadable glyph; it is not
  asserted as literal printed text. Affected blocks/fields are excluded from
  factual accuracy. No borrowing from similar invoices.
- Mirrored bleed-through is retained separately, with tentative/partial text and
  uncertainty descriptions. Unreadable foreground numbers remain null with issues.
- Numeric dates with two valid day/month interpretations remain ambiguous; Spanish
  language/location alone does not resolve them. Written month names and dates
  with only one valid interpretation can be normalized. Quantities/currency remain
  null where not printed. Printed arithmetic is never repaired.
- `complete` means every page/region was inspected and annotated, including
  explicitly unresolved regions; it does not mean all text was readable.
- Review provenance maps JSON Pointers to page/block/cell IDs outside invoice JSON.
  Metadata records author/source, status, hashes and inspected pages independently
  for the two stages. Model payloads contain no provider-specific OCR IDs.

## Install and validate

Run from the repository root. A separate environment avoids changing ingestion's
own dependencies:

```sh
uv venv benchmark/.venv
uv pip install --python benchmark/.venv/bin/python -r benchmark/requirements.txt
benchmark/.venv/bin/python -m benchmark validate
benchmark/.venv/bin/python -m pytest -c benchmark/pytest.ini benchmark/tests -q
```

PyMuPDF renders pages locally; no OCR is used in rendering. JSON Schemas live in
`schemas/`; regenerate them only when intentionally changing contracts with
`python -m benchmark.schema_definitions`. Invoice schema 0.1 uses exactly the
agreed fields, rejects extra properties, validates dates and decimal strings.

```sh
benchmark/.venv/bin/python -m benchmark validate-artifact invoice path/to/invoice.json
benchmark/.venv/bin/python -m benchmark validate-artifact reading path/to/reading.json
```

## Review the annotations

```sh
benchmark/.venv/bin/python -m benchmark render
benchmark/.venv/bin/python -m benchmark review-packet
open benchmark/review/index.html
```

The local packet links each original, full page image, reading JSON, invoice JSON,
provenance/status and unresolved issues. `review/manifest.json` and
`review/unresolved.json` are machine-readable. Page images are reproducible and
ignored by Git; JSON annotations and review packet are retained.

1. Inspect every original page, including small print and non-text regions.
2. Correct stage 2 literal transcription and stage 3 meanings separately. Update
   pointer provenance and issues alongside edits. Never take a model answer as
   ground truth without returning to the PDF.
3. Keep partial references `complete: false`. Use exclusions/issues for unresolved
   regions, never null as a guess that information is absent.
4. Refresh hashes as **draft** after edits (replace the example filename):

```sh
benchmark/.venv/bin/python -m benchmark refresh-draft --file-id '2026-01-08_P001.pdf' --stage stage2
benchmark/.venv/bin/python -m benchmark refresh-draft --file-id '2026-01-08_P001.pdf' --stage stage3
benchmark/.venv/bin/python -m benchmark validate
```

Only a human who actually completed review should run each appropriate attestation:

```sh
benchmark/.venv/bin/python -m benchmark attest-review --file-id '2026-01-08_P001.pdf' --stage stage2 --reviewer 'Your name' --attestation 'I personally inspected every page and verified this reference'
benchmark/.venv/bin/python -m benchmark attest-review --file-id '2026-01-08_P001.pdf' --stage stage3 --reviewer 'Your name' --attestation 'I personally inspected every page and verified this reference'
```

Codex has not run those attestation commands. Artifact edits invalidate saved
hashes; review status must be refreshed. Changes to a reading used by an isolated
run require a new run, because the original experiment used different evidence.

## Run the three tracks

Keys are read from environment variables or the existing root `.env` without
printing them. Never put keys in command arguments or commit `.env`/raw runs.
See [provider verification](providers/verification.md) for actual access evidence.

OCR needs `FAL_KEY`. Direct DeepSeek needs `DEEPSEEK_API_KEY`. Helmcode needs
`HELMCODE_API_KEY` and authenticated catalogue membership. Jev accepts
`TYPESAFE_API_KEY` or the existing `JEV_API_KEY`.

```sh
benchmark/.venv/bin/python -m benchmark catalogue --provider jev
benchmark/.venv/bin/python -m benchmark catalogue --provider helmcode
benchmark/.venv/bin/python -m benchmark catalogue --provider deepseek
```

Use explicit returned model IDs. `jev-latest` is an alias, not an immutable model
revision. Both requested and returned models are recorded. Unknown versions/costs
stay unknown. Provider contracts are documented but only actual runs count as live
evidence.

### 1. OCR: original PDF to canonical reading

```sh
benchmark/.venv/bin/python -m benchmark init-run fal-dev-v1 --track ocr --provider fal --model fal-ai/got-ocr/v2 --split development
benchmark/.venv/bin/python -m benchmark run fal-dev-v1
```

fal returns text strings only. Canonical readings have one `other` block per page,
with tables/block kinds/layout marked unsupported. Native strings and full request
bodies, including page data URIs, are retained in credential-redacted raw files.

### 2. Isolated interpretation: same canonical reading, two interpreters

Reviewed stage 2 readings are required by default. For current **draft** references,
use the explicit `--provisional` flag shown below. Both providers receive the same
canonical reading JSON. Neither receives stage 3 answers nor original images.
Execution code never opens stage 3 references. Candidates use only supplied blocks,
cells and their literal substrings; fields from reference answers are unavailable.

```sh
benchmark/.venv/bin/python -m benchmark init-run jev-dev-v3 --track isolated --provider jev --model jev-latest --split development --provisional
benchmark/.venv/bin/python -m benchmark run jev-dev-v3
benchmark/.venv/bin/python -m benchmark init-run deepseek-dev-v1 --track isolated --provider helmcode --model deepseek-v4-flash --split development --provisional
benchmark/.venv/bin/python -m benchmark run deepseek-dev-v1
```

For direct DeepSeek use `--provider deepseek --model <accessible-model-id>` in a
new run. The Helmcode adapter first requires the catalogue command above. Its
publicly documented model name is not proof your account can run it.

Jev enforces its observed 255-option API limit by selecting within groups of at most 252 source candidates plus three abstention choices, then comparing group winners. All candidates are offered; none are silently truncated. This tournament can still lose a correct candidate and remains part of pipeline accuracy.

Jev first selects scalar candidates and classifies source units, then assigns
fields within selected line/tax/additional-field units. Deterministic assembly
preserves unit order and does not collapse duplicates. Every value choice has
missing, none-of-the-above and ambiguous options. Raw answers and candidate pools
remain saved. Unassigned content remains in the input reading. A missing required
row label/description falls back to that unit's literal text with an explicit issue;
it is not silently treated as a successful field extraction.

### 3. End-to-end: saved actual OCR reading to both interpreters

```sh
benchmark/.venv/bin/python -m benchmark init-run jev-e2e-dev-v1 --track end_to_end --provider jev --model jev-latest --reading-run fal-dev-v1 --split development
benchmark/.venv/bin/python -m benchmark run jev-e2e-dev-v1
benchmark/.venv/bin/python -m benchmark init-run deepseek-e2e-dev-v1 --track end_to_end --provider helmcode --model deepseek-v4-flash --reading-run fal-dev-v1 --split development
benchmark/.venv/bin/python -m benchmark run deepseek-e2e-dev-v1
```

Failed upstream OCR yields explicit downstream failure, not a missing denominator.
Changing interpretation prompts never reruns OCR. Prompt/schema/config/source and
reading hashes are saved. Use a **new run ID** for changed execution code, model,
prompt or source. Completed HTTP subcalls are cached within a run. Raw requests
and each attempt response are retained; successful stage output is immutable.

```sh
benchmark/.venv/bin/python -m benchmark run jev-dev-v3 --retry-failed --max-attempts 2
```

Retries are bounded (1–5 per failed invocation). Rate-limit/server-error calls may
be retried. Transport timeouts have unknown server outcome and require explicit
retry; they may already have incurred cost. Ctrl-C leaves pending records/raw
attempts; repeat the run command to resume. Use `--limit 1` for a smoke run.
Single-writer CLI: do not execute the same run concurrently.

Keep held-out labels fixed. Develop only against `--split development`. Freeze
prompts/candidate rules, then create new runs with `--split held_out`; do not tune
against their reference answers. The reports retain split-level results.

## Import external outputs

Import already-run providers or OSS parsers without adding orchestration code:

```sh
benchmark/.venv/bin/python -m benchmark init-run imported-ocr --track ocr --provider import --model 'actual-provider/model-version' --split all
benchmark/.venv/bin/python -m benchmark import-output imported-ocr --file-id '2026-01-08_P001.pdf' --output canonical-reading.json --raw-request request.json --raw-response response.json --metadata operations.json
```

`operations.json` requires source SHA-256, latency seconds (number/null), usage
object, known cost USD (decimal string/null), attempts, and resolved model
(string/null). Example:

```json
{"source_sha256":"<64 lowercase hex characters>","latency_seconds":null,"usage":{},"cost_usd":null,"attempts":1,"resolved_model":null}
```

Interpretation imports use `--track isolated --provisional` or `--track end_to_end
--reading-run ...`; metadata additionally requires `reading_sha256`, the hash of
the canonical input reading serialized with this harness's `dump` format. Save the
exact reading, without answers/images, as the actual external provider input.
Imported metadata is an external attestation, not independently verified timing.
Use the reading schema for canonical conversion; retain native output separately.
Plain text stays plain text, not a made-up table. Invalid JSON is preserved and
recorded as invalid rather than dropped.

To represent an execution failure with no valid output:

```sh
benchmark/.venv/bin/python -m benchmark record-failure imported-ocr --file-id '2026-01-08_P001.pdf' --reason 'Provider request failed; raw evidence retained externally'
```

Prefer importing raw evidence when available. Never place credentials in reasons.
Local raw runs contain invoice contents; they are ignored by Git.

## Offline scoring and reports

These commands make **no API calls**:

```sh
benchmark/.venv/bin/python -m benchmark score jev-dev-v3
benchmark/.venv/bin/python -m benchmark report benchmark/reports/jev-dev-v3.json --output benchmark/reports/jev-dev-v3.md
benchmark/.venv/bin/python -m benchmark.examples.generate
```

Reports contain each expected invoice, its status, reviewed/provisional class,
reference coverage, per-field errors, line alignment, exclusions, operation
metadata, split aggregates and official reviewed filenames. Missing files, failed
providers and invalid schemas remain visible. An empty reviewed subset has no
accuracy score. Examples are clearly synthetic all-null or not-executed runs;
they are not fabricated provider results. The all-null example should have very
low present-value recall even though correct-null rates can be high.

### Scoring definitions and limitations

Stage 2 uses exact Levenshtein character and whitespace-token word error rates.
Strict text preserves literal Unicode and within-block whitespace; serialization
joins blocks with newline, table cells with tab, pages with newline/form-feed.
Whitespace-normalized scoring applies NFC and collapses Unicode whitespace to one
space. It does not remove punctuation, change case, repair numbers or accents.
Missing/extra word multisets give order-independent content recall/precision;
numeric/identifier tokens containing digits are matched exactly with multiplicity.
This cannot prove semantic completeness or assign shared tokens to the right block.

Ordered text distances and exact ordering flags are separate from content coverage.
Tables use one-to-one multiset row/cell matches including column and spans; table
identity and layout position are not scored. Block-kind results are unavailable
when the provider does not supply comparable kinds. No coordinate/layout accuracy
is fabricated. Uncertain reference blocks are excluded wholesale; provider text
cannot always be aligned to those regions, so exclusions may leave extra text in
predictions. Those documents need error review, not a claim of perfect alignment.

Stage 3 validates the complete strict schema before awarding factual credit.
Scalar exact matches retain spelling/case. Normalized text uses NFC/whitespace;
identifiers, invoice numbers, tax IDs, currency and ISO dates remain exact. Amounts,
quantities and rates use `Decimal`, never float tolerance; trailing decimal zeros
are equivalent. European decimal parsing is used for source candidates, but
noncanonical decimal strings in an invoice payload fail schema validation.

Collection alignment maximizes exact normalized non-null field agreements in a
one-to-one assignment (Hungarian algorithm); zero-overlap pairs remain unmatched.
No fuzzy threshold. Duplicate rows retain their multiplicity; order inversions
are reported separately. Correct-row precision/recall requires all scored row
fields to match. Matching based on a common amount alone can pair wrong descriptions;
field errors remain visible. Whole-document correctness also requires schema and
collection order/count correctness. Metadata fields and reference `issues` are not
factual accuracy targets; issues/exclusions instead drive separate abstention records.

Present-value recall and correct-null rate are separate. Unsupported non-null
means a value where a *complete* reference has null (including extra collections);
it is not an independent claim that the original document cannot support it.
Unresolved JSON Pointers and descendants are excluded. Incomplete/missing references
never imply absent content; they reduce reference coverage. Failures earn no factual
credit, including null credit. Reports provide macro averages and micro totals.

Jev candidate coverage is an **optimistic value-availability upper bound** across
source-only candidates, not proof a value was grouped into the right row. Conditional
assignment accuracy shows correctness for available values; final invoice scoring
still penalizes candidate generation, role routing, assembly, dropped/extra rows and
wrong ordering. Candidate metric coverage is reported when calls fail before assembly.

## Remaining gates

Human review is still required for official results. For live comparisons, supply
`FAL_KEY` (or import real OCR outputs) and either `HELMCODE_API_KEY` with catalogue
access or `DEEPSEEK_API_KEY`. Jev access alone does not demonstrate OCR or DeepSeek
performance. Live provider failures remain benchmark outcomes; local tests establish
harness behavior, not extraction quality or readiness for all 500 invoices.

## Recorded development baseline

[Latest Jev development report](reports/jev-dev-v3.md) evaluates 30 development
invoices against **Codex drafts**: 30 schema-valid outputs, 84.93% macro
present-value recall, 23.08% macro correct-null rate, and 0/30 documents with every
scored field and collection order correct. These are provisional measurements,
not human-verified extraction accuracy. Candidate value coverage is 98.95% as an
optimistic upper bound; high candidate availability does not guarantee correct
assignment. No prompts were tuned against held-out reference answers.

[Initial run](reports/jev-dev-v2.md) retains five HTTP 400 failures from the
previously undocumented 255-choice cap. The later run reused identical completed
HTTP requests and recorded new grouped-choice calls. Its reported 208.27 seconds
is cumulative known request latency, including inherited attempt evidence, not a
fresh end-to-end wall-clock measurement. Saved responses report 2,595,341 input
and 927,912 output tokens; billing cost remains **unknown**. The complete raw
requests/responses and candidate artifacts remain locally under the ignored
`runs/` directory. Repeating the completed run made no new provider calls.

DeepSeek, OCR and end-to-end provider comparisons have not been executed. Their
remaining access requirements are stated above. The held-out 20 invoices have draft
references but have not been sent for live evaluation.

## Supervised first-test gate: document instructions

Hidden PDF text must not become authoritative invoice evidence. The benchmark's
fal adapter sends rendered PNGs only, never the embedded text layer. A synthetic
regression verifies that adding a white-on-white instruction leaves the transmitted
pixels unchanged. Visible instruction-like text must remain literal document data;
removing every suspicious note would conceal real content and inflate completeness.
Both interpreters receive an explicit untrusted-document instruction. These offline
checks establish input boundaries, not proof of model resistance to injection.

For our first tests together:

1. Use synthetic/development cases first: clean invoice, invisible instruction,
   and visible instruction-like note. Inspect the original render, actual OCR
   input/output, interpreter requests, and both final JSONs side by side.
2. Check that hidden text did not enter the image-only reading, visible notes were
   retained as data, and instructions did not alter identifiers, IBANs, amounts,
   or produce payment decisions. Treat any such contamination as a failed test;
   investigate before expanding the batch. Preserve raw evidence of the failure.
3. Check ambiguous dates independently: `08/05/2026` remains unresolved unless the
   document itself supplies disambiguating evidence. Do not silently force a date.
4. Keep `FA-5044_mensajería2.pdf` held out. After freezing prompts and candidate
   rules, inspect this specific case during held-out evaluation without tuning
   against its answers. Its references remain drafts until human attestation.

Any future embedded-text/parser adapter needs its own visibility checks and must
keep invisible text separate from canonical visual evidence. The image-only
regression does not establish that guarantee for other adapters. No new live calls
are needed to run these offline tests.
