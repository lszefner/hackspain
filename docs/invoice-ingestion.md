# Invoice ingestion: discussion brief

Superseded by [the v-alpha implementation specification](ingestion-agent-alpha-spec.md), which records the later decision to use invoice JSON rather than domain tables, the seven-invoice inspection, and the independently implemented benchmark contract. The discussion below is historical context, not the current implementation contract.

Status: alpha scope aligned on 2026-09-19; OCR selection and implementation pending. Planning stays in the original conversation until the user requests handoff to a new conversation in this worktree.

## Objective

Design, document, and build an end-to-end pipeline that turns messy invoice PDFs into extractable data while preserving the evidence needed to verify each result.

Extract ALL document data from the start, including every line item, notes, footers, payment terms, bank details, references, stamps, and unusual fields. Correctness comes before cost or speed optimization. The user has not requested a spend cap; record usage and avoid unnecessary runs nevertheless.

## Agreed alpha decisions

- Interface: simplest working batch CLI with ingest, resume/retry, and JSONL export; no UI required.
- Storage: Supabase Storage for originals and large reading artifacts; Postgres for searchable evidence, interpretation results, provenance, and processing state. Keep original, reading, and interpretation layers independently versioned.
- Reading: integrate existing document OCR/layout technology, preferably available through the user's fal.ai or Helmcode access, or an established open-source pipeline. Do not build OCR from scratch. Embedded PDF text is supplementary evidence, not the complete solution.
- Interpretation: compare Jev Choice-based assignment plus deterministic assembly against DeepSeek structured extraction using identical persisted reading evidence. Jev candidates come from OCR blocks/cells; a schema alone does not generate candidates. Include missing/ambiguous options and preserve unassigned content.
- Completeness: retain all reading blocks and link interpreted values to evidence. Block accounting does not prove that OCR captured everything visible in the original PDF; check that separately.
- Benchmark: the user will select and manually annotate 50 invoices, including the hardest ones. Suggested split: 30 development and 20 held-out evaluation examples, covering hard examples in both. Keep manual ground truth independent of model suggestions.
- Metrics: reading accuracy/omissions, table structure, interpretation accuracy/completeness, unsupported values, whole-document correctness, latency, usage/cost, failures, and resume/retry behavior. Optimize cost/time after a working quality baseline.

## Non-goals

Payment decisions, business-rule authoring, ERP reconciliation, and spreadsheet ingestion are outside this workstream. Parsing quality checks belong here; deciding whether an invoice should be paid does not.

## Proposed representation

Use both a semi-structured evidence layer and a versioned structured invoice record.

- Preserve the original PDF, file identity and content hash, page text, layout/table information where available, and extraction method/version.
- Normalize common invoice fields: invoice number, dates, supplier/customer identities, currency, line items, tax breakdowns, totals, and payment details when present.
- Keep unusual fields and uninterpreted text in the evidence layer rather than dropping them to fit a rigid schema.
- Link extracted fields back to page evidence. Preserve original values alongside normalized values. Missing, ambiguous, conflicting, and unreadable values must remain distinguishable; never invent missing data.

The concrete schema and extraction tools should follow inspection of representative PDFs, including scans and difficult layouts. Filenames are not evidence of invoice contents.

## Proposed pipeline

1. Inventory files and assign stable identities, preserving one result per input even when content repeats.
2. Inspect pages and extract embedded text/layout; route pages needing OCR or visual interpretation through a fallback.
3. Map extracted evidence into the invoice schema.
4. Validate types, evidence coverage, and arithmetic consistency. Report discrepancies without silently correcting the document.
5. Persist results and per-file processing state so interrupted batches can resume and failed files can be retried.
6. Export JSON/JSONL; derive tabular exports later if needed.

Per-file results should distinguish successful extraction, extraction requiring review, and processing failure. Record attempts, stage, errors, elapsed time, and provider usage/cost where applicable.

## Evidence required before claiming correctness

- Profile the full corpus for page counts, text availability, layouts, and extraction failures.
- Manually verify a representative sample and hold back examples for evaluation.
- Measure field-level correctness and missing values, separately for header fields, line items, taxes, and totals.
- Account for every input file, including unreadable files and duplicates.
- Demonstrate interruption/resume, bounded retries, invalid extractor output handling, and provider failure if a provider is used.
- Report measured throughput and cost with the actual hardware and extraction configuration.

## OCR shortlist and unresolved verification

Public documentation checked on 2026-09-19:

- fal hosts GOT-OCR2 at https://fal.ai/models/fal-ai/got-ocr/v2. It advertises formatted-document and table OCR. Inspect its actual response for layout/provenance support and test Spanish invoice fidelity before selecting it.
- Helmcode exposes managed model inference through an OpenAI-compatible API: https://helmcode.com/ and https://dev.helmcode.com/docs. Public information does not establish which OCR/vision endpoints the user's account can run; verify its model catalogue during implementation.
- PaddleOCR is an established OSS document-processing alternative: https://github.com/PaddlePaddle/PaddleOCR. Assess deployment effort and structured output before choosing self-hosting.

Choose the first reader from actual sample output, preserving its complete native response. Provider marketing or general benchmark scores are not acceptance evidence for this corpus.

## Manual benchmark format

Before annotation starts, prepare a small template with: exact filename, page references, literal text and table rows/cells, intended field meanings, original and normalized values, additional content, and explicit unreadable/ambiguous regions. Do not correct arithmetic mistakes or infer absent values in the ground truth. This supports separate scoring of reading and interpretation rather than only checking totals.

## Next implementation slice

Inspect representative documents, build a corpus inventory, settle the extraction contract, then implement a small vertical slice from PDF to evidence-backed JSON. Use its measured failures to select fallbacks before processing the full corpus.
