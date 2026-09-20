Invoice ingestion: current state and a bounded path to 98% correctness at one-tenth the cost and time

Assessment date: 19 September 2026. Implementation snapshot: 93f9ced, configuration alpha-4, reader vision-regions-2. Document status: evidence-backed design and experiment plan; the optimized cascade is not implemented or benchmarked.

The requirement is at least 98% of invoices fully correct after bounded automatic escalation, not 98% of fields, not 98% of accepted invoices after excluding failures, and not an average OCR confidence of 98%. This supersedes the earlier 95% accuracy target. Separately, this document uses p95 latency to discuss processing-time tails; that is a different metric.

The intended progression is exactly: fast deterministic extraction → image extraction when necessary → a more precise targeted image check → explicit unresolved outcome if the source still cannot establish the answer. Retries are part of one invoice's processing budget and final score.

1. Assessment and recommendation

The current system is a useful correctness investigation harness, but an inefficient default production path. It sends every page through image models, reads each detected document layer twice, asks a model to reconcile the readings, then uses a reasoning model to build the JSON. It does this even when a PDF already contains clean text.

The strongest measured opportunity is the actual corpus: 471 of 500 invoices have embedded text on every page; 29 are image-only. Native extraction can therefore potentially remove vision from most inputs. However, text presence is not proof of complete extraction, and it does not establish a 90% deterministic invoice pass rate.

Recommended architecture:





Make deterministic native-text/layout extraction and deterministic validation the default.



Accept only complete, supported invoices; missing text, conflicting assignments or unaccounted visual content trigger escalation.



Use one existing vision model to resolve the first failed quality gate and produce structured evidence, rather than always making separate layout, dual-reading, reconciliation and interpretation calls.



Escalate only unresolved regions to a second, more precise image attempt. Reuse correct rows and evidence; do not regenerate the entire invoice unnecessarily.



Stop after bounded attempts. Preserve ambiguity and source provenance. Never meet the latency target by silently accepting guesses.



Prove the end-to-end invoice target on independent labels. Keep all retry cost, unresolved cases and false acceptances in the evaluation.

Feasibility judgment: a 10× reduction in average variable processing work and average latency is plausible for this text-heavy corpus, but unproven. A 10× reduction in the monthly bill is a separate question because the existing provider may be flat-rate. A 10× reduction in tail latency requires the expensive path to be sufficiently rare and bounded. None of those claims follows from the current 40/40 core-field result.

2. What exists today, at each level







Level



Implemented state



Material limitation





Interface



Python CLI: ingest, status, resume, retry, interpret saved readings, export, preflight, offline fixture



No production review UI or continuously operated service demonstrated





Inputs



Folder or explicit manifest, file IDs, source hashes, one outcome per input



No full-corpus quality baseline; filenames are not extraction evidence





Original evidence



Immutable PDFs and rendered page images



Enlarging a low-resolution original cannot recover missing detail





Rendering



PDFium, every page, intrinsic rotation respected; CLI default 200 DPI, hard-case tests 300 DPI



Rendering/vision happen even when usable native text exists





Native text



Extracted and retained as auxiliary page metadata



Not used as a trusted fast extraction route





Layout



Qwen identifies foreground and mirrored-background regions



Model-generated region geometry, not accurate word/line boxes





Reading



Gemma reads region views; Qwen independently reads them



Repeated image submissions, possible shared hallucinations





Comparison



Qwen compares both readings against region images; every initial block must be accounted for



The comparison can accept a wrong character or miss uncertainty





Interpretation



DeepSeek maps canonical reading into invoice JSON and pointer-to-source evidence



High reasoning-token usage; mappings vary between runs





Alternative adapters



fal GOT OCR and Jev remain available



They are not the default; the original hard-pair run performed badly





Schema



Version 0.1 invoice and reading schemas, frozen into runs



Schema validity does not prove document completeness or factual accuracy





Validation



Types, source references, linked factual leaves, foreground/background separation, essential invoice fields, currency-code shape, issue/uncertainty gates



No calibrated guarantee that a completed invoice is fully correct





Arithmetic



Decimal-based observation of printed base plus listed taxes versus total



A warning, not a universal accounting identity or a correction engine





Persistence



Private Supabase Storage and Postgres, immutable artifacts, attempt/job state, leases



Synchronous storage/database work inside the async pipeline adds serial work





Recovery



Bounded transport retries, unknown-outcome handling, saved readings, job reuse



A vision-stage retry can repeat completed model work; ambiguous submission must not be retried blindly





Observability



Attempt events, network/persistence time, raw responses, reported usage, artifact hashes



True billed cost and full per-invoice queue-to-durable-result timing are not established





Benchmark



50 selected reference invoices, scoring, saved reports, additional corpus census



References include draft/uncertain material; five evaluated invoices are far too few for a 98% invoice claim





Integration



Evidence-linked JSON export



No ERP reasoning, payment decision, or downstream reasoning acceptance demonstrated

The private worker and model calls have been exercised live. The evidence does not establish deployment readiness, multi-tenant isolation for a future service, or a reliable 500-document operating SLO. Originals, references and generated outputs must remain distinct; reference answers and filenames must never enter inference.

Current invoice representation

The JSON contains invoice number, date, purchase-order reference, currency, supplier/customer identity and location/address, IBAN, ordered line items, taxes, taxable base and total, annotations, additional fields and issues. Quantities and amounts are decimal strings. Missing values are null. Evidence is a separate mapping from JSON Pointers to page/block references, optionally character offsets.

Rows with identical descriptions remain separate. Quantities are not invented as one. Currency and dates must follow supported normalization rules. Notes, stamps, footers and unusual fields must not disappear because they do not fit the standard fields.

The reading currently exposes semantic block kinds but not genuine table-cell geometry. Coordinates in the sidecar describe a region, not exact text bounds. A downstream component cannot safely treat those boxes as word-level evidence.

Current call graph

flowchart TD
    A[PDF and manifest] --&gt; B[Preserve original; render every page]
    B --&gt; C[Qwen: identify document regions]
    C --&gt; D[Gemma: read each region]
    D --&gt; E[Qwen: independently read the same region]
    E --&gt; F[Qwen: compare drafts against images]
    F --&gt; G[Canonical reading and layout sidecar]
    G --&gt; H[DeepSeek: invoice JSON and evidence]
    H --&gt; I[Validation and durable outcome]

For P pages and R_p processed layer groups on page p, the normal call count is:

calls = Σ_p (1 layout call + 3 × R_p) + 1 interpretation call

A single page without background therefore takes 5 calls. A single page with foreground and mirrored background takes 8 calls. In the current code, detections of the same layer are grouped into a crop; this formula concerns processed layer groups, not every individual detected rectangle. Calls within a document are substantially sequential.

Each region call receives a full-region view plus three strips. This means 13 submitted image views for a one-layer page and 25 for a two-layer page. These are repeated views, not 13 or 25 distinct pages.

3. What the measurements actually prove

Accuracy history







Evaluation



Observed result



Valid interpretation





Original GOT OCR + Jev, hard pair



5/40 scored core fields; billed/tax rows lost



The initial approach failed these cases





First replacement automatic reader



39/40 core fields



Large improvement, incomplete reading





Fresh repeat of that reader



39/40, with a different missed field



Instability matters even when aggregate score is unchanged





Independent-read/comparison version, hard pair



40/40 core fields; all 5 billed rows and 2 tax rows



Success on the selected core-field denominator, not full invoice correctness





Same frozen version, 3 additional invoices



58/59 core fields



Useful transfer evidence; one explicit city missing from its structured field





First structure-prompt correction



57/59 on those saved readings



Recovered city but introduced currency/location regressions





Final contract clarification



59/59 on the 3 saved readings; 40/40 on the hard pair



Development regression success, not a new independent test

The two hard invoices were selected from the final filename group, not established as the chronologically newest invoices. All five have now influenced development. Reinterpreting saved readings tests reconstruction; it does not independently retest image recognition.

The core metric excludes uncertain draft-reference fields and annotations, and focuses on reference values that are present. It does not adequately penalize every possible invented optional field or unverified background statement. Decimal normalization also makes 430.5 and 430.50 equal. That is reasonable for amounts, but is not character-exact OCR.

Known remaining defects include unflagged ambiguity in the blurred tax-ID digit, unreliable small-note/footer transcription, and incorrect or incomplete background text. A result marked completed can still contain such an error. A manually or AI-reviewed correction is not counted as automatic accuracy.

There is no measured 98% whole-invoice accuracy today. The code tests—59 application tests and 34 benchmark tests at the recorded checkpoint—exercise software behavior, not a representative invoice population.

Latency and token workload

The table is recomputed from the saved first version-2 live runs, not inferred from the final answer text. It excludes earlier experimental reruns.







Invoice



Calls on successful path



Reading seconds



Interpretation seconds



Sum of successful stage times



Submitted image views



Reported total tokens





scan_023.pdf



8



88.54



57.66



146.21



25



50,458





scan_025.pdf



8



89.45



43.97



133.42



25



49,579





2026-01-16_P004.pdf



5



51.09



38.64



89.73



13



32,332





FA-4816_seguridad.pdf



5



33.68



41.86



75.54



13



33,349





scan_021.pdf



5



62.37



44.80



107.17



13



35,410

Important measurement boundaries:





The noisy scan required a failed HTTP attempt before the successful path above. Its full processing cost/time is higher.



The hard-pair run spanned 150.51 seconds from first reading attempt start to final interpretation completion with two documents processed concurrently. Do not add concurrent durations and call that batch wall time.



The additional-three run spanned 346.70 seconds, including the failed attempt and retry.



Stage timers exclude initial ingestion/upload, some final publication work, export and queue waiting. They are not a production latency percentile.



Summing token counts across different models is a workload proxy. Their token accounting and image encoding need not be comparable; the sum is not a bill.



Failed calls may have no usage returned. Missing usage is not zero usage.

For the hard pair, DeepSeek reported 19,937 reasoning tokens out of 22,488 completion tokens: about 88.7%. But that is only about 19.9% of the pair's total reported token workload across all calls. Removing this reasoning alone cannot deliver a 10× whole-pipeline reduction.

The logs report about 30.15 seconds of request/response persistence intervals across 16 calls for the pair. This is a sum across concurrent work, includes application/database/storage overhead, and is not a clean measurement of the latency that an async-storage rewrite would save. It nevertheless shows that repeated durable round trips are significant.

4. Corpus composition and the 98% constraint

A local metadata census—no provider calls and no reference-value inspection—found:







Property



Count





PDFs



500





Pages



522





Single-page invoices



478





Two-page invoices



22





PDFs with some embedded text on every page



471, or 94.2%





Image-only PDFs



29, or 5.8%





PDFs with a mixture of text-bearing and text-empty pages



0





Unique PDF byte hashes



500

Text can still be partial, misordered, stale, hidden, or inconsistent with visible marks. This census identifies fast-path candidates, not proven safe fast-path invoices. The 22 multi-page documents also need independent validation; the measured five-document sample does not cover them.

For 98% of these 500 invoices to be fully correct, at least 490 must pass. At most 10 can be incorrect, failed or unresolved; those are not three separate budgets.

If every text-bearing invoice were correct, at least 19 of the 29 scans would also have to be correct. If text-invoice correctness is only 99%, the required scan success is roughly 82% in expectation; at 98% text correctness, nearly all scans must succeed. At 97% text correctness, even perfect scan extraction cannot reach 98% overall.

The native source of scan_023 is only 496 × 701 pixels, with no text layer; scan_025 is 1654 × 2338, also with no text layer. Some characters in the first are genuinely ambiguous. If more than 10 of the 500 invoices have information that cannot be established from the available source, this exact all-input 98% target is physically unattainable without better originals or trusted external evidence. The benchmark must reveal that, not hide it through exclusions.

5. Define invoice correctness before optimizing

Define a per-invoice indicator Y_i = 1 only when the final automatic result is complete and correct against independently adjudicated source evidence. Then:

whole-invoice accuracy = Σ Y_i / number of all submitted invoices

An invoice passes only if:





All foreground supplier/customer identities, invoice identifiers, dates, references, bank details and currency are correct where printed.



Every billed row is present exactly once, with correct description, quantity when printed and amount. Repetition and order are preserved.



Every tax component, printed subtotal/base and total is correctly represented; printed inconsistency is preserved and flagged rather than “fixed.”



Relevant foreground notes, stamps, footer information and unusual fields are preserved and correctly attributed.



No unsupported value is invented. Null is correct only when genuinely absent; a missing printed value is a failure.



Evidence identifies the correct page and source span/region. A valid pointer to the wrong text is a failure.



Unresolved required source details do not become confident values.

Foreground document completeness remains in scope. The system should preserve evidence of a separate background document without forcing a speculative complete transcription of that unrelated document into the foreground invoice. When document membership itself is uncertain, escalation is required. This boundary must be fixed in the annotation guide before evaluation; it is not permission to drop foreground footers or inconvenient text.

For the headline metric, a review-required or genuinely unreadable invoice is not a fully correct automatic invoice. Correct abstention is a valuable secondary metric, but does not inflate the 98% numerator. Human correction after extraction is reported separately.

Track these secondary metrics alongside the headline:







Metric



Why it matters





Automatic acceptance coverage



An extractor that rejects everything cannot qualify





Precision among accepted invoices



Measures the danger of an incorrect completed result





Critical-field false acceptance



Protects downstream reasoning from wrong identities, payment details and amounts





Complete line-set and tax-set accuracy



Field averages can hide missing rows





Unsupported-value rate



Captures hallucinated optional fields and annotations





Uncertainty detection and appropriate abstention



Separates model failure from insufficient source quality





Success after each quality stage



Shows which escalation actually recovers invoices





Accuracy by text/image, layout family, page count, difficulty



Prevents a good aggregate from hiding a broken segment

Acceptance coverage times accepted-result precision equals whole-invoice automatic success under this definition. 98% accepted at less than 100% precision is below 98% correct. Design for margin: for example, 99% acceptance at 99.5% precision yields 98.505% correct, before considering sampling uncertainty.

For intuition only, if an invoice has 20 independently correct fields, 98% accuracy per field gives only 0.98^20 ≈ 66.8% fully correct invoices. Real errors are correlated, but the example explains why the field score cannot stand in for the invoice target.

6. Proposed bounded quality cascade

flowchart TD
    A[Preserve original; inspect every page] --&gt; B[Stage 0: deterministic native extraction and assembly]
    B --&gt; C{Complete supported invoice and all gates pass?}
    C --&gt;|Yes| D[Durable validated JSON]
    C --&gt;|No| E[Record exact gaps and ambiguity]
    E --&gt; F[Stage 1: image or text-assisted extraction for the failed evidence]
    F --&gt; G{All required details now established?}
    G --&gt;|Yes| D
    G --&gt;|No| H[Stage 2: targeted higher-detail image check]
    H --&gt; I{Conflicts resolved from source?}
    I --&gt;|Yes| D
    I --&gt;|No| J[Needs review; preserve candidates and source]

Stage 0 — deterministic extraction, zero LLM calls when it works





Read native PDF words/spans with page coordinates and order information. Preserve every page and immutable source hashes.



Build the canonical reading from real spans. Reconstruct rows using geometry and printed labels, not filename or supplier-specific expected answers.



Assemble unambiguous fields deterministically. Reuse decimal/date/IBAN normalization only when its preconditions hold. Do not guess a currency that is not printed or a quantity that is absent.



Account for page regions, notes, tables and footers. A cheap local render/coverage inspection can identify visual content outside native-text spans. This check is a candidate signal that must itself be validated; text presence alone is insufficient.



Apply completeness, type, evidence, duplicate-row, cross-page and conditional arithmetic checks.



Accept only supported assignments. Ambiguous competing totals or addresses, unmatched rows, unreadable characters, suspicious text order and visible unaccounted content cause escalation.

The desired “90% fast deterministic” rate is a hypothesis to measure, not something the census has proved. Deterministic parsing can also be deterministically wrong. It needs its own independent false-acceptance audit and continuing sampling of apparently clean invoices.

Prefer general rules for labels, geometry and normalization over bespoke templates for the five evaluated documents. Do not use exact invoice strings, expected totals, file hashes or benchmark labels as inference rules. Supplier-specific learned templates, if ever introduced, need their own held-out suppliers/layouts and provenance; they are not required for the first implementation.

Stage 1 — one bounded recovery attempt

When native evidence is missing or unreliable, submit the relevant page/region to an existing available vision model, initially testing Gemma and Qwen in separate ablations. Produce extraction and field assignments together where that preserves traceability; avoid a mandatory separate DeepSeek step.

When the text is complete but field assignment is ambiguous, a compact text-only call may be more appropriate than an image call. The route follows the actual failure, not a blanket rule that more images are always better.

The response should contain compact observed text blocks plus field/row references, or an equivalent invoice-plus-evidence envelope. Code validates and assembles the persisted reading/invoice contracts. Do not eliminate the reading layer merely to save tokens; reduce redundant serialization and repeated calls instead. Empty/default schema fields can be filled deterministically, but absent business facts cannot.

Keep correctly extracted fields and rows. The recovery attempt returns a bounded set of corrections/additions with evidence. A merge must fail visibly on conflicting source assignments, changed row identity or a request to discard a previously accounted block.

Do not fully transcribe mirrored background material on the normal path. Preserve the original region and classify it separately. Only inspect it further when membership is uncertain or necessary to disambiguate the foreground. This does not license ignoring foreground marks.

Stage 2 — targeted precise image recovery

Escalate only unresolved fields/regions, but preserve enough surrounding context to identify the party, row or document layer. Examples: a tax-ID and supplier header, an IBAN line, a table boundary, or a footer region.

Use the highest useful source resolution. Higher DPI helps vector text or an originally high-resolution raster; it cannot restore detail lost in a 496 × 701 image. Preserve original and deterministic contrast/deskew variants, with recorded transforms. Do not use generative image enhancement to invent readable characters.

An independent reading from a different existing model is appropriate here. It should see the source before a leading draft when practical. Reconciliation must preserve disagreement and every affected original block. Agreement between models is supporting evidence, not proof.

The final merge re-runs the entire invoice's structural and evidence checks. A corrected total can change tax consistency; a corrected row can affect row count and order. A local crop success is not a complete invoice success until the merged result passes.

Stop rule and budget

Start with three quality stages total: deterministic, first recovery, precise recovery. The default target is at most two quality-related cloud calls on a single-page invoice that fails the deterministic route. Multi-page or multi-region exceptions require explicit budgets and separate reported cohorts; they cannot hide an unbounded loop inside one “attempt.”

Transport retries are a different mechanism. Start by testing at most one known-safe retry for a transient failed submission, under a shared per-document call/time/token budget. An ambiguous timeout after submission requires request-status recovery where supported or an explicit unknown outcome—not automatic duplicate billing. The existing three-attempt behavior is the baseline, not a requirement to keep multiplying calls in every new stage.

Illustrative deadline budget: about 3 seconds local work, 8 seconds first recovery and 25 seconds precise recovery, plus measured queue/persistence overhead. These are engineering targets to validate, not provider promises. If a deadline is reached, persist the unresolved state; never mark an incomplete invoice complete to meet it.

7. Make retries diagnostic and inspectable







Trigger



Next action



What must not happen





No native text or substantial unaccounted image content



Page/region vision extraction



Treat an empty reading as a complete invoice





Native text exists but party/total assignment conflicts



Targeted text or image assignment with local context



Re-OCR every page without a reason





Missing billed row or quantity/amount alignment



Table-region image recovery; preserve existing row IDs



Collapse duplicate descriptions or assume quantity one





Two readings disagree on an identifier



Precise crop and independent reading



Pick the plausible digit or “repair” using a checksum





Printed amounts do not reconcile



Inspect evidence and record printed discrepancy



Change a printed number merely to make arithmetic work





Unclear foreground/background membership



Wider contextual image inspection



Transfer a background supplier or bank account into the main invoice





Invalid/truncated JSON



Bounded format/output recovery only if source content is intact



Repeat expensive OCR for a serialization failure





Known HTTP 429/5xx failure



Respect endpoint policy and bounded backoff



Retry forever or launch parallel duplicate submissions





Unknown result after submission



Recover request status or persist unknown



Assume nothing was billed





Genuine source ambiguity after precise inspection



Review with source crop and supported candidates



Turn model agreement into a verified fact

Persist the reason for every escalation, affected pointers/block IDs, source artifact hashes, parent attempt, model/settings, request ID when available, usage, latency, and merge result. The next stage should receive the precise unresolved question, not a vague request to “try harder.”

The routing threshold is calibrated against whole-invoice labels, especially false acceptance. Do not use a model's self-reported confidence as a calibrated probability or a standalone exit gate.

8. What “10× cheaper” means under the existing account

Separate four quantities:





Marginal model charges: extra money charged for a request or token.



Allocated subscription cost: a share of a fixed monthly account cost.



Infrastructure work: inference time, token workload, storage, requests and worker CPU/memory.



Operational cost per correct invoice: all of the above plus exception handling and human review.

Helmcode's public pricing describes Qwen/Gemma as flat-rate rather than per-token, while DeepSeek consumes a plan allowance. Our actual account tier, invoice and overage terms have not been inspected. Therefore reducing reported tokens cannot honestly be translated into a measured euro saving here. Provider pricing.

The same account exposes other providers' models, but their presence in a catalogue does not establish paid access. The provider distinguishes its own-model plans from frontier models paid from prepaid credit. The proposal uses only the already-working Qwen, Gemma and DeepSeek access; it does not depend on buying a new model or service. Provider model/access documentation.

The public rate-limit page lists 100 requests/minute, 5 concurrent requests and 2M language-model tokens/minute per key. Other public pricing text lists different allowances for some models. Treat these as planning hints, not verified limits of this account. Check actual account settings and response behavior before changing concurrency. More concurrency improves throughput; it does not make an individual serial call chain 10× faster. Provider rate-limit documentation.

Cost equations

For truly metered inference, with verified account/model rates:

C_models = Σ_attempts (uncached_input_tokens × input_rate + cached_input_tokens × cache_rate + output_tokens × output_rate) / 1,000,000

Add cache-write or image-specific charges only when the actual billing contract requires them. Include failed/billed attempts. Do not double-count reasoning tokens if they are already included in billed output tokens.

For fixed-price access:

allocated cost per correct invoice = (allocated monthly fixed fee + variable charges + storage/worker cost + review cost) / correct invoices delivered

If the subscription fee and volume stay the same, using fewer tokens may improve capacity without reducing the bill at all. If fixed cost represents more than 10% of today's total and remains unchanged, eliminating every variable cost still cannot produce a 90% reduction in total spend. A 10× monetary reduction therefore needs a baseline bill, a defined allocation policy and potentially a smaller plan or higher useful throughput; no plan change is proposed without that evidence.

For review, measure actual time. A model cascade that saves pennies but sends many more invoices to people may increase total cost. Conversely, hiding review outside the denominator can make an uneconomic system look cheap.

Why the reduction must come from architecture





Eight model calls becoming one is an 8× call-count reduction, not automatically 10× cost savings.



Disabling reasoning saves only the relevant portion of workload; it does not remove repeated images or storage.



Native deterministic extraction can eliminate model calls entirely for accepted text documents. That is the strongest route to a corpus-wide reduction beyond 10× in variable inference work.



Avoiding background transcription and repeated views removes work without pretending the foreground invoice has fewer fields.



Warm caching does not prove cold performance. All 500 current PDFs have distinct byte hashes; byte-identical caching cannot accelerate their first processing pass.

9. Quantitative cascade model

The following is a feasibility scenario, not a measured prediction. Stage costs are incremental, because escalated invoices pay for earlier stages too. Cost units normalize the current variable cost of processing an invoice to 1; the assumed ratios are not known Helmcode prices.

Let:





a0, a1, a2 be fractions of all invoices accepted at deterministic, first-image and precise-image stages.



Stage reach fractions be 1, 1 − a0, 1 − a0 − a1.



p0, p1, p2 be the true precision of those accepted results, measured from independent labels.

Then:

A = a0×p0 + a1×p1 + a2×p2

E[T] = T0 + (1−a0)×T1 + (1−a0−a1)×T2

E[C] = C0 + (1−a0)×C1 + (1−a0−a1)×C2

These formulas omit queueing/overload effects; measure and add them in live load tests.

Target scenario







Stage



Share of all invoices reaching it



Share accepted here



Precision assumption



Incremental time assumption



Incremental variable-cost assumption





Deterministic



100%



90%



99.8%



3 s



0.02





First recovery



10%



7%



99%



8 s



0.15





Precise recovery



3%



2%



99%



25 s



0.50





Still unresolved



—



1% of inputs, not accepted



—



Earlier attempts already counted



Earlier attempts already counted

Calculated consequences:





Whole-invoice accuracy: 98.73%, not the 99% acceptance rate.



Mean serial processing time before queueing: 4.55 seconds.



Paths finish at 3, 11 or 36 seconds in this simplified model. p95 is 11 seconds, not the mean of stage percentiles.



Variable-cost ratio: 0.05, or a hypothetical 20× reduction.



Mean remote quality calls: 0.13 per invoice if stage 0 is genuinely local and each later stage uses one call. Transport retries and multi-page exceptions add to this.

For these assumptions to achieve a 10× reduction, the matched baseline mean must be at least 45.5 seconds, and the matched baseline p95 at least 110 seconds. Our selected timings are compatible with that possibility but do not establish those corpus-wide baselines. Actual stage latencies also have distributions; the three discrete values above understate real tail complexity.

Stress scenario

Suppose only 80% are accepted deterministically, 12% after first recovery, 6% after precise recovery and 2% remain unresolved. With precision 99.8%, 99.5% and 99%, and incremental times 3, 12 and 35 seconds:





Accuracy becomes 97.72%: it fails the 98% target even though 98% are accepted.



Mean time is 8.2 seconds.



p95 rises to 50 seconds because 8% reach the last stage.



Variable-cost ratio is 0.09 under the same cost assumptions: nominal cost reduction can pass while accuracy and tail latency fail.

This is why a retry cascade needs both calibrated exit gates and an escalation-rate budget. It cannot rely on “we usually get it right after a few tries.”

If more than 5% require a slow final stage, that stage will generally affect p95. It still cannot be removed merely to improve the number: the remedy is to improve earlier-stage accuracy, make recovery cheaper, or revise an infeasible performance claim explicitly.

The calculations and confidence examples are reproducible in scenario-calculations.json.

10. Specific changes, ranked by expected value







Priority



Change



Why it matters



Proof required before promoting





1



Whole-invoice evaluator and independent gold labels



Prevents optimizing a misleading field score



Exact invoice pass/fail, missing/extra rows, false accepts and source ambiguity counted





1



Native-text/geometry route



Removes unnecessary image calls on most corpus candidates



Full foreground completeness, including notes and two-page inputs





1



Deterministic assembler with explicit ambiguity



Zero model cost for unambiguous native documents



High accepted-result precision across unseen layouts; no filename/reference rules





1



Bounded escalation controller



Pays for expensive reading only after a diagnosed failure



Recovery contribution, stage reach rate, total attempts/cost/time per invoice





2



Single-call image extraction plus assignments



Removes mandatory layout/dual-read/reconciliation/DeepSeek chain



Same evidence and completeness contract; no hidden row loss





2



Targeted crop recovery



Reduces repeated full-page input and anchoring



Identifiers and row attribution remain correct with surrounding context





2



Avoid routine reasoning model use



Current interpretation spends most completion tokens reasoning



Test available non-thinking/text extraction settings; verify actual response behavior





2



Compact output and deterministic defaults



Avoids repeatedly generating boilerplate schema structure



No truncated invoice, omitted field, or weakened evidence





2



Async persistence and shared HTTP sessions



Reduces avoidable serial round trips and connection setup



Profiling before/after; cancellation/recovery tests; no premature completion





3



Persist successful subcalls for retry reuse



Prevents repeating layout/reading after later-stage HTTP failure



Exact source/model/prompt/schema keys; no reuse of partial responses





3



Cross-run content/version cache



Useful for reprocessing and downstream work



Cold/warm results separate; input provenance preserved; tenant-safe boundaries if a service is added





3



Controlled parallelism



Better throughput under account limits



Queue-inclusive p95, throttling and failure rates at realistic load

Reasoning control requires an experiment

The vision adapter already sends enable_thinking: false; suggesting that flag alone is not a new optimization there. DeepSeek uses the existing reasoning model and the observed responses contain substantial reasoning usage. The exact mechanism for disabling or reducing reasoning on this Helmcode deployment has not been verified. Its accessible examples/API-reference pages could not be fetched during this assessment, so this document does not invent an accepted parameter.

Test the actual account behavior in a bounded development experiment. If the endpoint ignores or rejects the control, use the already-accessible Qwen/Gemma text mode or deterministic assembly for the fast route. Keep DeepSeek as a measured exception only if it adds enough accuracy. Reducing max_tokens blindly can truncate the answer; reported reasoning/completion accounting also needs checking against the provider's budget semantics.

Persistence and cache changes must preserve the evidence contract

Today each remote call persists request and response artifacts; storage uploads are followed by download/hash verification, and synchronous database/storage operations run inside the async workflow. Move blocking work out of the event loop, reuse HTTP connections, batch safe metadata operations and deduplicate repeated image payload storage where possible. Preserve durable submission intent and verify required artifacts before publishing success.

A useful representation stores an image once by hash and references it in an audit envelope, with a reproducible request recipe; the actual provider request can still contain the required bytes. Do not replace auditability with a log line that cannot reconstruct what was sent.

Current job settings include per-input identity. Do not assume existing content-addressed artifacts already give a general cross-batch inference cache. Separate reusable computation identity from per-input outcomes, and include source, reading, model/revision, prompt, schema, preprocessing and normalization versions. Model aliases without immutable revisions remain a reproducibility limit.

Avoid introducing a distributed orchestration stack before profiling the existing worker. The initial gains come from removing work, not adding infrastructure.

11. Benchmark design for a reasoning-critical parser

Ground truth and independence





Treat all five evaluated invoices as development/regression cases. Keep their failures visible; do not relabel them as fresh validation.



Audit the existing 50 reference records. They contain draft/uncertain annotations and known background-reference discrepancies. An unreviewed draft is not reliable gold for a 98% claim.



Establish labels for all business fields, complete line/tax sets, relevant annotations, extra unsupported fields, evidence attribution and source readability.



Have a reviewer work from the original, without seeing model predictions first. Adjudicate disagreements and critical identifiers independently. A second LLM agreeing with the first is not independent human ground truth.



Reserve a locked evaluation set from invoices not used for tuning. Split by layout/supplier/template family where possible so near-identical templates do not masquerade as broad generalization.



Stratify text versus image, one versus two pages, number of rows, repeated rows, stamps/notes, blur and background interference. Because many scans are already in the original selected set, truly fresh hard examples may be scarce; report this and obtain more source examples before making broad hard-scan claims.

The full 500-document census may be used to design the split. Reference values may not be used to route or repair an inference result. Synthetic corruptions are useful development stress tests, but transformed copies of training documents are not independent held-out invoices.

The actual acceptance gate

For a fixed labeled corpus of 500, the empirical target is at least 490 fully correct final automatic invoices. Count timeouts, failures and review-required outputs in the denominator. Do not credit eventual manual correction as automatic success.

On a smaller locked set, require the same 98% empirical rate and publish its uncertainty. Do not imply that a small passing sample proves a future-population guarantee. If the business needs a one-sided 95% statistical lower bound of at least 98%, the requirements are stricter:







Independent representative invoices



Correct results needed for that lower bound





100



Impossible even at 100/100





150



150/150





200



200/200





300



299/300





500



496/500

These are exact binomial examples, not a mandate to redefine the user's empirical 98% benchmark as 99%+. Related templates violate the simple independence assumption; evaluate cluster effects and segment results. A census of the fixed 500 tells us their realized result directly, while claims about future invoices need representative sampling.

Compare on the same inputs and include every attempt

Freeze the current implementation and the optimized candidate. Use the same source hashes, labels, provider access, load, hardware, normalization policy and inclusion rules. Measure:





Fully correct invoices / all submitted invoices.



Incorrect completed results and unresolved results separately.



Which stage first produced a correct result, and which stage mistakenly accepted an incorrect one.



All calls, input/output/cache/reasoning usage, missing usage, retries and billed cost where available.



Queue-to-durable-result mean, p50, p95 and p99; also per-stage and per-model time.



Batch throughput at fixed concurrency. Report it separately from per-document latency.



Mean cost per submitted invoice and per correct invoice, including unsuccessful work and review cost separately.

For latency, report terminal outcomes for all inputs, not just fast successes. Also report the joint fraction fully correct automatically within the target deadline; a fast failure must not make the system look faster and better. Do not average route p95 values to calculate overall p95.

Measure cold unique inputs separately from repeated cached inputs. Repeat enough runs at comparable load to expose model variability and provider queueing. Do not select the best of several outputs after checking gold; any production selection rule must run without reference answers, and all its attempts count.

Evaluation experiment matrix







Experiment



Isolates



Promotion rule





Frozen current baseline



Actual invoice accuracy and cost/time reference



Labels and measurement complete; no claim from field-only scoring





Native reading + current interpreter



Benefit/risk of skipping vision



No loss of source completeness; measured latency saving





Native deterministic assembly



Zero-model fast route



Accepted-result precision high enough for cascade target





Native reading + compact non-reasoning model



Alternative for semantic ambiguity



Better cost/accuracy frontier than current reasoning interpreter





One-call vision extraction



Removes serial image work



Correct rows/annotations/evidence on scanned development cases





Targeted precise recovery



Incremental value of expensive stage



Recovers enough additional whole invoices per unit cost/time





Complete bounded cascade



The actual deployable policy



≥98% empirical invoice correctness plus matched cost/time gates





Repeated runs and realistic concurrency



Stability and operational tail



No hidden timeout/overload regression or false-acceptance increase

Experiments change one major factor at a time where practical. A quick benchmark on known examples is a development check, not the release evaluation.

12. Instrumentation needed before claiming 10×

The raw information exists in pieces, but needs a consolidated per-invoice measurement record. Add or derive:





Source hash, page count, native-text assessment and route eligibility signals.



Start of input handling, queue wait, render/native extraction duration, each model request interval, persistence duration, final durable-publication time.



Quality stage and escalation reason, affected fields/regions, number of calls and retry type.



Requested and resolved model, provider revision when available, prompt/schema/preprocessing versions.



Actual usage categories and billing source; keep unavailable cost null.



Final status, independently scored invoice-correct indicator, field/row diagnostics and any manual intervention.



Cache hit type and bytes reused; distinguish provider prefix caching from complete extraction reuse.

Request and response contents stay in private artifacts; operational events do not need invoice text or credentials. The final measurement joins through stable input/attempt IDs so failed attempts cannot disappear from aggregates.

Define before the experiment:

cost reduction = matched baseline total cost per input / candidate total cost per input

mean latency reduction = matched baseline mean / candidate mean

p95 latency reduction = matched baseline p95 / candidate p95

A successful “10×” report identifies which cost definition is used and meets each claimed ratio. A 10× token reduction is not relabeled as a 10× bill reduction. A 10× throughput increase from more workers is not relabeled as a 10× per-invoice latency reduction.

13. Delivery sequence and estimated effort

These are engineering estimates for one engineer familiar with the repository, not measured project durations. Labeling and missing-source resolution can be the critical path.







Work package



Estimated engineering effort



Concrete exit artifact





Lock metric, review label quality, instrument invoice-level measurement



1–2 days



Whole-invoice evaluator, immutable split, baseline measurement format





Native text/geometry reading and conservative deterministic assembly



2–4 days



Fast route with source spans, completeness failures and regression tests





First image recovery producing structured evidence



1–3 days



Existing-provider adapter, compact envelope, source-linked merge





Targeted precise retry controller



2–3 days



Bounded stage machine, explicit escalation reasons and budgets





Async persistence, session reuse and retry-subcall reuse



1–3 days



Profiled reduction with recovery/durability tests





Paired evaluation, threshold calibration and shadow operation



2–4 days



Accuracy/cost/latency report including all attempts and tail failures

Total: roughly 9–19 engineering days, with some overlap possible. Independently reviewing hundreds of invoices is additional work; budget reviewer time separately and measure it on an initial batch. Do not spend the labeling budget polishing already-exposed examples while leaving the locked set unreliable.

Order matters: establish the correctness metric first, then test the native route. If native completeness cannot be made reliable, stop assuming a 90% deterministic acceptance rate and re-run the cost model with observed stage rates. If the 98% target is blocked by unreadable originals, resolve source acquisition rather than spending unlimited inference on them.

Rollout should begin in shadow mode: preserve the current path, run the candidate without overwriting outcomes, adjudicate disagreements, and compare the two policies. Promote the candidate behind a versioned configuration only after the agreed gates pass. Retain an explicit fallback version; do not silently change models or prompts for an accepted run.

14. Failure conditions and decision rules

The proposal fails its claim if any of the following occurs:





It achieves 98% only after removing scans, failed calls, unreadable invoices or manual reviews from the denominator.



It achieves field accuracy while missing complete rows, annotations or printed optional values.



The cheap route accepts errors that the router cannot detect; successful downstream reasoning then starts from false facts.



More expensive retries are frequent enough that mean cost or tail latency misses the budget.



The reported saving depends on duplicate cache hits, a different input mix, more hardware, or a different billing allocation.



A smaller output budget silently truncates invoices, or persistence optimization publishes results before evidence is durable.



It uses a supplier/filename-specific answer lookup, benchmark references, or new paid access to appear successful.

Responses to failure are specific: improve the faulty gate or extraction stage, measure another existing-model configuration, acquire clearer source evidence, or state that the simultaneous target has not been achieved. Do not weaken correctness to preserve a speed headline.

The current strict-source blocker on the blurred hard invoice remains real. This document changes the population-level design target to 98%; it does not retroactively certify every ambiguous character in that individual invoice.

15. What changes now versus what this document proposes

This task produced a current-state assessment, a local corpus census, recomputed runtime evidence, a reproducible scenario model and a proposed optimization/validation sequence. It did not deploy the cascade, change the production extractor, run new model inference, buy access, or establish 98% invoice accuracy.

The next implementation slice is: whole-invoice evaluator + native-text deterministic route + failure-reason reporting, followed by a small development comparison. That will answer whether the proposed 90% cheap-stage acceptance rate is realistic before building the full retry policy.

16. Evidence, code map and reproduction

Local evidence





Original benchmark: initial OCR/Jev failures.



Repair audit: replacement results, regressions, broader sample and source limitations.



Final hard-pair field regression.



Frozen broader evaluation.



Final broader development regression.



Native source-fidelity audit.



Full corpus metadata census.



Recomputed runtime measurements.



Scenario and statistical calculations.



Automatic JSON artifacts, kept separate from explicitly reviewed artifacts.

Implementation ownership







Concern



Primary source





CLI and configuration



cli.py, config.py





PDF rendering and auxiliary text



pdf.py





Vision crops, independent reading and comparison



vision.py





Structured interpretation



deepseek.py





Assembly orchestration, request logging and reuse



pipeline.py





Storage, database, job identity and durability



storage.py, migration





Invoice/evidence validation



validation.py, contracts.py





Source-bound explicit corrections



review.py





Scoring and exclusions



scoring.py, audit_bundle.py





Schemas



invoice.json, reading.json

The early alpha specification and older price/speed handoff contain historical assumptions about fal/Jev and pending live trials. Use this assessment and the current code for the present default, not those historical statements.

Recompute the evidence locally, without provider calls:

benchmark/.venv/bin/python -m benchmark.profile_optimization

The script reads local PDFs, verifies hashes on available raw export artifacts, aggregates the saved events and calculates the hypothetical cascade. Raw run exports are intentionally gitignored. Without those exports, the script explicitly reports unavailable runs. Running it overwrites the local report files, so preserve the accompanying historical measurement snapshot before reproducing on a machine without the raw exports. It does not recreate missing runs or call models.

External documentation was checked on 19 September 2026. Public provider pages are not the account's invoice or a guarantee of supported inference controls. No external per-token price has been substituted for this account's unknown effective billing.