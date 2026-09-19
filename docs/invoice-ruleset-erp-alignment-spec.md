# Invoice–ruleset–ERP alignment: traceable decision-context specification

Date: 2026-09-19

Status: implemented and verified offline; see `docs/decision-context-integration.md` for the
authoritative implemented contract. This document remains the design rationale; where its
illustrative excerpts differ from `rules_ingestion/decision_context.schema.json`, the machine
schema is authoritative.
Scope: challenge 1, semantic alignment and traceability between structured invoice extraction and business-rule inputs.

## 1. Recommendation and intended outcome

Preserve extraction schema 0.1 as immutable evidence. Add one versioned `DecisionContext` boundary between extraction and rule evaluation instead of putting authoritative master/business data into extracted invoice JSON.

The boundary must answer:

1. What was observed?
2. Which transformation produced this business-rule input?
3. Which source is authoritative for this particular fact?
4. What is missing, ambiguous, unavailable, or unverified?
5. Can every enabled rule obtain the inputs and evaluator capabilities it actually needs?

### What completing this work achieves

We will have a traceable, typed connection between three previously imperfectly aligned domains:

- **Invoice:** what the document says, normalized without silently inventing or discarding facts.
- **Ruleset:** what each frozen rule requires, the exact fields bound to those requirements, and what cannot yet be evaluated.
- **Master/ERP:** which supplier and order were resolved, what their designated sources assert, and whether the relevant ERP state was actually available.

For one invoice, the system will be able to explain: “Rule R uses total 121.00 from this invoice JSON pointer, supplier P042 resolved through this master row, and order state PENDIENTE from this ERP snapshot, evaluated against this exact ruleset and date.” It will also be able to say: “This rule cannot be evaluated because currency is missing, supplier identity is ambiguous, the ERP snapshot is incomplete, or the evaluator does not support the declared semantics.”

This is an **auditable input and readiness layer**, not yet a complete or certified payment-decision engine. Successful delivery means a faithful join with explicit uncertainty, not guaranteed factual correctness of extraction or guaranteed correctness of every existing check.

It enables the next stage to produce defensible PAGAR, ESCALAR, or NO_PAGAR decisions. It does not itself complete that stage, execute payments, or make a note on an invoice authoritative.

### Non-goals

- Payment execution. Persistence is no longer a non-goal: an append-only private
  `ingestion.decision_contexts` index plus immutable artifact bytes in the existing private
  Supabase Storage bucket is now part of the implemented boundary (see
  `docs/decision-context-integration.md`).
- Reliable natural-language rule compilation and arbitrary rule-code generation: challenge 2.
- Implementing a JEV/DeepSeek second-pass reviewer: challenge 3.
- Rewriting all canonical check bodies or final verdict precedence in challenge 1.
- A universal tax, currency-conversion, identity-resolution, or rules platform.
- Changing provider prompts, benchmark references/reports, or extraction schema 0.1.

## 2. Repository findings at design time (historical; implementation changes below)

These were the observations at design time, distinct from the proposed contract below.

| Area | Current behavior | Consequence for this design |
| --- | --- | --- |
| `benchmark/schemas/invoice.json` | Source-of-truth schema 0.1; closed objects; decimal strings; many nullable scalars | Preserve the extracted artifact unchanged |
| Invoice fields | Identity, date, order reference, currency, supplier/customer, payment IBAN, lines, taxes, totals, annotations, additional fields, issues | Notes belong in annotations; uncertainty belongs in issues |
| `benchmark/schemas/reading.json` | Pages and stable block/row/cell identifiers, text, tables, uncertainties | Reading remains evidence, not an alternative deterministic business input |
| `ingestion/pipeline.py` | Interpretation normalizes and validates invoice plus evidence | Carry the outcome and quality artifacts through the adapter |
| `ingestion/validation.py` | Runtime evidence maps invoice JSON pointers to page/reference IDs, with optional spans; accepts a direct map or `pointers` wrapper | Do not substitute benchmark reference provenance for this runtime contract |
| `ingestion/export.py` | Exports invoice, reading, evidence, layout, coverage, checks, warnings, raw artifacts separately with hashes | Reuse artifact references and hashes instead of embedding duplicate payloads |
| `rules_ingestion/invoice.py` | Flat invoice dict; Decimal amounts; missing currency defaults to EUR; invalid line values are dropped | New boundary must not inherit these lossy defaults |
| `webui/map_invoice.py` | Maps nested invoice to flat fields; sums every tax into IVA, substitutes zero for missing tax amounts, leaves vendor ID null, drops document type/annotations/issues | Replace the semantic boundary, not merely rename fields |
| `rules_ingestion/checks.py` | Six canonical categories; `(inv, master, params) -> (PASS\|FAIL\|NEEDS_REVIEW, reason)` | Preserve signature through a guarded projection |
| Master identity | Vendor lookup can use NIF; duplicate keys use vendor ID | Resolve a stable supplier identity before duplicate matching |
| `rules_ingestion/loader.py` | Normalized lookups collapse duplicate primary keys using last-write-wins | Retain original candidates before collapse or ambiguity becomes invisible |
| `rules_ingestion/sources.yaml` | Supplier columns do not include active status; order columns do not include currency | Those facts remain missing unless explicitly sourced |
| Schema 2.0 rules | Built by `rules_ingestion/merge.py`; use `id`, `input_fields`, `params`, and `condition` | Freeze the actual artifact and bind to `id` |
| Rule conditions | Known Python check, structured clauses, or pending; new rules may need new fields | Unknown dependencies and unsupported semantics must remain explicit |
| Canonical aliases | `amount`/`total`, `importe`/`total`, `fecha`/`date` | Resolve through a narrow versioned registry |
| Current dispatch | Canonical-only; skips unknown categories, ignores structured conditions, reads `rule_id` instead of merged `id` | Follow-on evaluator blocker; cannot treat skipped work as PASS |
| Current decision reducer | Any FAIL becomes NO_PAGAR | Follow-on blocker: balanced policy requires FAIL to follow each rule's `on_fail` |
| `webui/master_data.py` | ERP errors can become an empty state map; date is hardcoded | Carry availability/coverage explicitly and require evaluation date |
| `webui/results_store.py` | Duplicate history comes from processed revisions | Processing is not approval or payment evidence |

No deployed final `rules.json` example was established during the design handoff. The schema 2.0 description comes from its builder, not an assumed deployed artifact.

## 3. Architecture and immutable boundaries

```text
Immutable extraction artifacts             Frozen external context
invoice + reading + evidence + checks      master + ERP + history + ruleset
                    \                         /
                     DecisionContext builder
                              |
                 normalized facts + lineage
                 dependency/capability preflight
                              |
                 guarded legacy-check projection
                              |
                    future decision evaluator
```

Store references and hashes to source artifacts rather than duplicate invoice, reading, whole master tables, and rule definitions inside every context. Carry the normalized facts needed by rules and their explanation.

Snapshot construction is part of the boundary, but the storage backend is not specified here. Snapshots must be retrievable and immutable for replay; a path to a mutable live file is insufficient.

Artifact hashes refer to the actual referenced bytes. Reuse exported hashes when referencing exported files; do not confuse those with benchmark-specific reserialization hashes. Newly created JSON snapshots use the repository's stable `ingestion.contracts.canonical_bytes` convention. Capture ruleset bytes once so the label `v3` cannot conceal changed rule content.

The evaluation date is a required input. Retrieval time, source effective time, and invoice issue date are separate concepts. A timestamp/hash establishes which snapshot was used, not that its source was current or correct.

## 4. Proposed contract names and types

The notation below specifies data shapes, not implementation code. These contracts are now
implemented; `rules_ingestion/decision_context.schema.json` is the authoritative machine
schema and this section's notation is conceptual.

Primitive names:

- `ISODate`: valid `YYYY-MM-DD` date.
- `ISODateTime`: timezone-qualified timestamp.
- `SHA256Hex`: 64 hexadecimal characters identifying referenced bytes.
- `JSONPointer`: RFC 6901 pointer; `""` refers to the document root.
- `SourceId`, `FieldId`: stable strings within the context and registry respectively.
- `DecimalString`: finite base-10 decimal string, never a JSON floating-point value.

### 4.1 DecisionContext

```text
DecisionContext {
  schema_version: "decision-context/1"
  context_id: string
  adapter_version: string
  registry_version: "decision-fields/1"
  evaluation_date: ISODate

  sources: map<SourceId, Source>
  extraction: {
    invoice: SourceId
    reading: SourceId
    evidence: SourceId
    checks: SourceId
  }
  ruleset: {
    source: SourceId
    schema_version: "2.0"
    ruleset_version: string
    policy_id: string
  }

  fields: map<FieldId, Fact>
  rule_bindings: RuleBinding[]
  findings: Finding[]
  reviews: ReviewRecord[]
  preflight: {
    data_status: "ready" | "blocked"
    execution_status: "supported" | "blocked"
  }
}
```

Additional extraction artifacts, such as coverage and warnings, may be referenced in `sources` and findings without copying their contents. The context has no payment destination and no final payment verdict in this first version.

### 4.2 Source

```text
Source {
  kind: "invoice" | "reading" | "evidence" | "extraction_checks"
      | "supplier_master" | "order_master" | "erp"
      | "history" | "ruleset" | "extraction_auxiliary"
  availability: "available" | "partial" | "unavailable"
  artifact_ref: string | null
  sha256: SHA256Hex | null
  captured_at: ISODateTime
  as_of: ISODateTime | null
  asserted_by: string
  authoritative_for: string[]
  scope: string
}
```

Rules:

- Available sources require retrievable immutable bytes and a matching hash.
- Unavailable sources have no invented artifact or hash; `captured_at` records the failed acquisition attempt.
- A partial snapshot can have a valid hash but cannot prove absence beyond its captured coverage.
- `as_of: null` means source effective time is unknown, not current.
- `scope` describes the relevant population/coverage and, for history, its event meaning and boundaries.
- `asserted_by` identifies the responsible source/extraction run, not an automatically inferred human verifier.
- `authoritative_for` is assigned by trusted integration configuration. Invoice text and model confidence cannot grant authority.
- Authority is field-scoped: the supplier master can own registered bank details without owning ERP payment state.

Master snapshots preserve original workbook hash, sheet, row/cell locations, and normalized record pointers. A normalized snapshot hash alone identifies what was consumed but not the original workbook cell that asserted it.

### 4.3 Fact and lineage

```text
Fact<T> {
  value: T | null
  state: "present" | "missing" | "ambiguous" | "invalid" | "unavailable"
  basis: "observed" | "normalized" | "derived"
  extraction_quality:
      "no_reported_issue" | "uncertain" | "unlinked" | "not_applicable"
  transform: string
  inputs: InputRef[]
}

InputRef =
  { source: SourceId, pointer: JSONPointer }
  | { field: FieldId }
```

| State | Meaning |
| --- | --- |
| `present` | A usable typed value is available; this alone does not establish authority or verification |
| `missing` | An available structured source has no usable value |
| `ambiguous` | Multiple candidates remain; value is null and candidate/issue references are retained |
| `invalid` | A source value exists but violates the normalized field's type or format |
| `unavailable` | The required source could not be obtained |

Non-present facts have null normalized values; original observed values/candidates remain in their immutable sources. A disagreement between two clear observations does not make either observation ambiguous: preserve both and record the relationship as a finding or rule result.

Transform identifiers are versioned and deterministic. Derived facts reference every contributing input, and lineage must terminate in hashed source artifacts without cycles. A hash does not itself confer authority. Derived identity retains its dependence on invoice extraction even when its resulting ID came from a master record.

Quality propagates through dependencies; a calculation does not clear uncertainty. `no_reported_issue` is deliberately weaker than “correct” or “verified.” A missing value can have no reported extraction issue and still be unusable.

Decimals remain strings at JSON boundaries and use `Decimal` internally. No silent rounding, zero substitution, EUR substitution, or locale guessing on already-normalized extraction decimals.

### 4.4 Findings and reviews

```text
Finding {
  code: string
  severity: "info" | "blocking"
  field_ids: FieldId[]
  rule_ids: string[]
  refs: InputRef[]
}

ReviewRecord {
  subject: FieldId | InputRef
  aspect: "transcription" | "source_validity" | "business_authorization"
  state: "verified" | "rejected" | "needs_review"
  actor_id: string
  actor_kind: "human" | "system"
  reviewed_at: ISODateTime
  evidence: InputRef[]
}
```

Review records are scoped to the immutable context/source version being reviewed. A changed source/context cannot inherit a prior verification without an explicit applicable record. No review record means unreviewed.

Review workflow implementation is not part of challenge 1. These shapes reserve an explicit seam; they do not permit a model or anonymous caller to mint business authorization.

## 5. Narrow field registry

A small static registry aligns meanings and supported aliases. It is not a user-programmable field platform.

```text
FieldDefinition {
  id: FieldId
  type: string
  meaning: string
  allowed_source_kinds: string[]
  required_authority_scope: string | null
  legacy_aliases: string[]
}
```

| Proposed field | Type / meaning | Legacy alias or projection |
| --- | --- | --- |
| `invoice.document_type` | `invoice`, `credit_note`, `other`, or `unknown` | None |
| `invoice.number` | string | `invoice_number` |
| `invoice.supplier_tax_id` | string; invoice's asserted tax identifier | `nif` |
| `invoice.iban` | string; account printed on invoice | `iban` |
| `invoice.order_reference` | string | `pedido` |
| `invoice.issue_date` | ISO date | `date`, `fecha` |
| `invoice.currency` | currency-code string | `currency` |
| `invoice.taxable_base` | decimal string; printed taxable base | `base` |
| `invoice.total` | decimal string; printed total, not automatically payable balance | `total`, `amount`, `importe` |
| `invoice.vat_amount` | decimal string; derived from identified VAT rows only | `iva` |
| `invoice.lines.*.amount` | decimal string; preserve indexed position | Ordered `line_items` projection |
| `invoice.taxes.*.kind` | `vat`, `withholding`, `other`, or `unknown` | None |
| `invoice.taxes.*.amount` | decimal string; retain original sign | None |
| `supplier.id` | uniquely resolved master identifier | `vendor_id` |
| `supplier.tax_id`, `supplier.iban` | master assertions, separate from invoice | Master projection |
| `supplier.active` | boolean, only from an authoritative source | No fabricated default |
| `supplier.payment_terms_days` | integer explicitly parsed from master terms | Master projection |
| `order.id`, `order.supplier_id` | resolved order identity/owner | Master projection |
| `order.total`, `order.currency` | decimal string and currency code | Master projection |
| `erp.order_payment_status` | `PENDIENTE` or `PAGADA`; order-scoped | `erp_estado` |
| `history.processed_matches` | list of references to matching processing records | Restricted compatibility |
| `history.approved_matches` | list of references to explicit approval records | None |
| `history.paid_matches` | list of references to payment records | None |

The `*` denotes an indexed family such as `invoice.lines.0.amount`, not arbitrary field discovery. Each indexed amount retains its own lineage; the legacy list is a projection, not a second authoritative collection.

An empty history-match list is only a meaningful negative finding when the source was available, coverage sufficient for the question, and identity/matching inputs usable. Otherwise the fact is unavailable, ambiguous, or accompanied by a blocking coverage finding.

New rules requiring unregistered fields remain unbound. Do not interpret arbitrary `additional_fields` labels as trusted inputs or guess aliases by semantic similarity.

Annotations, issues, and reading blocks remain accessible through extraction references; do not duplicate them wholesale into `fields`.

## 6. Rule bindings and readiness

```text
RuleBinding {
  rule_id: string
  rule_pointer: JSONPointer
  dependencies: [{
    requested_name: string
    field_id: FieldId | null
    state: "bound" | "missing" | "ambiguous" | "invalid"
         | "unavailable" | "unknown_field"
  }]
  input_status: "ready" | "blocked"
  execution_status: "supported" | "unsupported"
  reason_codes: string[]
}
```

The rule pointer is relative to the frozen ruleset artifact. Use merged rule `id`, not a guessed or regenerated identifier.

Dependencies are the union of:

1. Declared `input_fields`.
2. Structured-condition field references.
3. Fields named by parameters such as required fields and duplicate keys.
4. The known canonical implementation's effective input requirements for enabled parameters.

An empty `input_fields` list does not prove a rule has no dependencies. An unknown field is not the same as a known field with a missing value.

Binding identifies a typed source; input readiness also requires suitable authority, extraction quality, coverage, and semantic compatibility. An unsupported condition or parameter remains an execution blocker even when its fields are available.

### Approval safety invariant

No PAGAR unless every enabled rule is accounted for, required inputs are usable, required semantics are supported, and no blocking review finding remains.

- A skipped rule cannot disappear from accountability.
- A blocked rule is not PASS.
- An empty result list is not “all passed.”
- Data readiness is not a decision or authorization.
- Known mismatches should be evaluated under rule policy, not mislabeled as missing data.
- Review blockers prevent PAGAR; they do not override a separately justified NO_PAGAR under the later precedence implementation.
- Disabled rules remain in the frozen ruleset but do not become unexplained enabled-rule omissions.

`MISSING` is a deliberate exception to ordinary value requirements: it can inspect a known missing state. Other checks cannot use that absence to skip comparisons and claim PASS.

## 7. Concrete JSON examples

These are synthetic excerpts of the proposed contract, not current runtime outputs, complete executable fixtures, or payment decisions. Angle-bracket hashes are documentation placeholders, not valid SHA256Hex values. Unshown fields and sources are omitted for readability; a complete executable context must contain every referenced source, field, and dependency.

### 7.1 Clean invoice: matching bank details and traceable VAT

```json
{
  "schema_version": "decision-context/1",
  "context_id": "ctx-clean-001",
  "adapter_version": "decision-adapter/1",
  "registry_version": "decision-fields/1",
  "evaluation_date": "2026-09-19",
  "sources": {
    "inv": {
      "kind": "invoice",
      "availability": "available",
      "artifact_ref": "stage3/clean-001.json",
      "sha256": "<invoice-sha256>",
      "captured_at": "2026-09-19T10:00:00Z",
      "as_of": null,
      "asserted_by": "extraction-run:extract-001",
      "authoritative_for": [],
      "scope": "Structured observations of clean-001.pdf"
    },
    "suppliers": {
      "kind": "supplier_master",
      "availability": "available",
      "artifact_ref": "snapshots/suppliers-001.json",
      "sha256": "<supplier-snapshot-sha256>",
      "captured_at": "2026-09-19T09:59:00Z",
      "as_of": null,
      "asserted_by": "configured-supplier-master",
      "authoritative_for": ["supplier.identity", "supplier.bank_details"],
      "scope": "Complete configured supplier table; original row locations retained"
    }
  },
  "fields": {
    "invoice.supplier_tax_id": {
      "value": "B12345678",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "no_reported_issue",
      "transform": "tax_id_format/1",
      "inputs": [{"source": "inv", "pointer": "/supplier/tax_id"}]
    },
    "supplier.id": {
      "value": "P042",
      "state": "present",
      "basis": "derived",
      "extraction_quality": "no_reported_issue",
      "transform": "unique_exact_tax_id_match/1",
      "inputs": [
        {"field": "invoice.supplier_tax_id"},
        {"source": "suppliers", "pointer": "/records/7/id"},
        {"source": "suppliers", "pointer": "/records/7/nif"}
      ]
    },
    "supplier.tax_id": {
      "value": "B12345678",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "not_applicable",
      "transform": "tax_id_format/1",
      "inputs": [{"source": "suppliers", "pointer": "/records/7/nif"}]
    },
    "invoice.iban": {
      "value": "ES9121000418450200051332",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "no_reported_issue",
      "transform": "iban_whitespace_uppercase/1",
      "inputs": [{"source": "inv", "pointer": "/payment/iban"}]
    },
    "supplier.iban": {
      "value": "ES9121000418450200051332",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "not_applicable",
      "transform": "iban_whitespace_uppercase/1",
      "inputs": [{"source": "suppliers", "pointer": "/records/7/iban"}]
    },
    "invoice.taxable_base": {
      "value": "100.00",
      "state": "present",
      "basis": "observed",
      "extraction_quality": "no_reported_issue",
      "transform": "identity/1",
      "inputs": [{"source": "inv", "pointer": "/totals/taxable_base"}]
    },
    "invoice.vat_amount": {
      "value": "21.00",
      "state": "present",
      "basis": "derived",
      "extraction_quality": "no_reported_issue",
      "transform": "sum_explicit_vat_rows/1",
      "inputs": [
        {"source": "inv", "pointer": "/taxes/0/label"},
        {"source": "inv", "pointer": "/taxes/0/amount"}
      ]
    },
    "invoice.total": {
      "value": "121.00",
      "state": "present",
      "basis": "observed",
      "extraction_quality": "no_reported_issue",
      "transform": "identity/1",
      "inputs": [{"source": "inv", "pointer": "/totals/total"}]
    }
  },
  "rule_bindings": [
    {
      "rule_id": "R_VENDOR",
      "rule_pointer": "/rules/0",
      "dependencies": [
        {"requested_name": "vendor_id", "field_id": "supplier.id", "state": "bound"},
        {"requested_name": "nif", "field_id": "invoice.supplier_tax_id", "state": "bound"},
        {"requested_name": "master.nif", "field_id": "supplier.tax_id", "state": "bound"},
        {"requested_name": "iban", "field_id": "invoice.iban", "state": "bound"},
        {"requested_name": "master.iban", "field_id": "supplier.iban", "state": "bound"}
      ],
      "input_status": "ready",
      "execution_status": "supported",
      "reason_codes": []
    }
  ],
  "reviews": []
}
```

`R_VENDOR` illustrates a known canonical rule with supported effective parameters, not a claim that the entire current balanced profile is supported. `master.nif` and `master.iban` are explicit compatibility requirements, not assumed existing `input_fields` strings.

The source tax label in this example is explicitly `IVA`; the transform records the label and amount. It does not classify VAT from a rate alone. A derived sum covers the supplied structured rows, not proof that the original PDF contained no other taxes.

The context's extraction references join invoice pointers to runtime evidence, for example:

```json
{
  "/payment/iban": [
    {"page": 1, "reference_ids": ["p1-bank"]}
  ],
  "/taxes/0/label": [
    {"page": 1, "reference_ids": ["p1-tax-label"]}
  ],
  "/taxes/0/amount": [
    {"page": 1, "reference_ids": ["p1-tax-amount"]}
  ]
}
```

Both evidence and reading artifacts are referenced and hashed. There is no need to repeat their entire link maps on each field. The adapter supports the existing direct evidence map and `{"pointers": ...}` wrapper. Runtime IDs may identify blocks, rows, or cells; optional character spans must retain the existing validation semantics.

Evidence linkage proves where a claim was linked, not factual accuracy or image completeness. Existing coverage explicitly measures OCR block linkage, not source-image completeness.

### 7.2 Different IBAN plus a new-account note

This excerpt also includes missing currency, an ambiguous date, and unavailable ERP state to demonstrate distinct states. These are additional problems, not consequences of the IBAN mismatch.

```json
{
  "schema_version": "decision-context/1",
  "context_id": "ctx-bank-change-002",
  "evaluation_date": "2026-09-19",
  "sources": {
    "erp": {
      "kind": "erp",
      "availability": "unavailable",
      "artifact_ref": null,
      "sha256": null,
      "captured_at": "2026-09-19T10:05:00Z",
      "as_of": null,
      "asserted_by": "configured-erp-bridge",
      "authoritative_for": ["order.payment_status"],
      "scope": "Requested order PO-0042; acquisition failed; no status established"
    }
  },
  "fields": {
    "invoice.iban": {
      "value": "ES6621000418401234567891",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "no_reported_issue",
      "transform": "iban_whitespace_uppercase/1",
      "inputs": [{"source": "inv", "pointer": "/payment/iban"}]
    },
    "supplier.iban": {
      "value": "ES9121000418450200051332",
      "state": "present",
      "basis": "normalized",
      "extraction_quality": "not_applicable",
      "transform": "iban_whitespace_uppercase/1",
      "inputs": [{"source": "suppliers", "pointer": "/records/7/iban"}]
    },
    "invoice.currency": {
      "value": null,
      "state": "missing",
      "basis": "observed",
      "extraction_quality": "no_reported_issue",
      "transform": "identity/1",
      "inputs": [{"source": "inv", "pointer": "/currency"}]
    },
    "invoice.issue_date": {
      "value": null,
      "state": "ambiguous",
      "basis": "observed",
      "extraction_quality": "uncertain",
      "transform": "preserve_unresolved_issue/1",
      "inputs": [
        {"source": "inv", "pointer": "/issue_date"},
        {"source": "inv", "pointer": "/issues/0"}
      ]
    },
    "erp.order_payment_status": {
      "value": null,
      "state": "unavailable",
      "basis": "observed",
      "extraction_quality": "not_applicable",
      "transform": "identity/1",
      "inputs": []
    }
  },
  "findings": [
    {
      "code": "INVOICE_MASTER_IBAN_DIFFER",
      "severity": "info",
      "field_ids": ["invoice.iban", "supplier.iban"],
      "rule_ids": ["R_VENDOR"],
      "refs": []
    },
    {
      "code": "UNREVIEWED_ANNOTATION",
      "severity": "blocking",
      "field_ids": [],
      "rule_ids": [],
      "refs": [{"source": "inv", "pointer": "/annotations/0"}]
    },
    {
      "code": "ERP_UNAVAILABLE",
      "severity": "blocking",
      "field_ids": ["erp.order_payment_status"],
      "rule_ids": ["R_DUPLICATES"],
      "refs": []
    }
  ],
  "rule_bindings": [
    {
      "rule_id": "R_DATES",
      "rule_pointer": "/rules/3",
      "dependencies": [
        {"requested_name": "date", "field_id": "invoice.issue_date", "state": "ambiguous"}
      ],
      "input_status": "blocked",
      "execution_status": "supported",
      "reason_codes": ["AMBIGUOUS_INPUT"]
    }
  ],
  "reviews": [],
  "preflight": {
    "data_status": "blocked",
    "execution_status": "supported"
  }
}
```

The separately referenced invoice artifact contains these excerpts:

```json
{
  "annotations": [
    {
      "kind": "note",
      "text": "Hemos cambiado de cuenta. Pagar al nuevo IBAN ES66 2100 0418 4012 3456 7891."
    }
  ],
  "issues": [
    {
      "field": "/issue_date",
      "kind": "ambiguous",
      "raw_text": "03/04/2026",
      "candidates": ["2026-04-03", "2026-03-04"]
    }
  ]
}
```

The note's evidence links `/annotations/0/text` to its reading block, for example page 1, `p1-note`.

Interpretation:

- Both IBANs can be clearly observed while disagreeing.
- The master remains authoritative for registered bank details.
- The note proves only that a bank-change claim was observed.
- No `payment_destination_iban` is produced by challenge 1.
- With otherwise complete inputs, the bank comparison can execute and fail. Under balanced policy, that failure should lead to ESCALAR, not automatic NO_PAGAR.
- The mismatch finding is informational at the data layer because it is a known comparison result, not an inability to obtain inputs. Its business consequence belongs to the applicable rule.
- For the small first version, an annotation without explicit disposition blocks automatic approval. This is a conservative review gate, not a bank-note classifier. It can escalate benign notes/footers too; reducing that burden is later policy/reviewer work.
- Merely verifying that the note was transcribed correctly does not authorize new bank details or clear a bank-change authorization requirement.

## 8. Mapping and missing/conflict behavior

| Source | Normalized field/context | Transformation | Provenance | Missing/conflict behavior |
| --- | --- | --- | --- | --- |
| `supplier.tax_id` | `invoice.supplier_tax_id` | Explicit formatting normalization; no digit correction | Invoice pointer → evidence → reading | Null/uncertain identifier cannot establish identity |
| Supplier master rows plus invoice tax ID | `supplier.id` | Exact normalized tax-ID match to one unambiguous supplier | Invoice identifier, matched/candidate rows, snapshot hash | Zero matches unresolved; multiple IDs/conflicting rows ambiguous; no name-based auto-selection |
| Duplicate master IDs | Supplier candidate context | Preserve rows before collapse; identical semantic duplicates may be grouped with all references | Workbook hash, sheet, row/cells, snapshot pointers | No resolution through last-write-wins |
| Invoice PO reference plus order master | `order.id`, `order.supplier_id` | Exact reference lookup; compare owner separately | Invoice pointer and order-row references | Missing/multiple order or conflicting supplier linkage blocks dependent checks |
| Invoice/master IBAN | `invoice.iban`, `supplier.iban` | Whitespace removal and uppercase independently | Each source separately | Missing is not equality; disagreement never overwrites either |
| `taxes[]` | Indexed tax kind/amount; `invoice.vat_amount` | Narrow explicit label mapping; sum identified VAT amounts only | Every contributing label/amount pointer | Unknown classification or missing contributing amount prevents a trustworthy aggregate; empty taxes is not zero VAT |
| Withholding/other taxes | Indexed kinds/amounts | Preserve original sign; do not infer sign from label | Original tax-row pointers | Never include withholding in IVA; mixed/unsupported structures block legacy base-plus-IVA reconciliation |
| Printed base/total | `invoice.taxable_base`, `invoice.total` | Strict decimal validation; preserve observations | Individual pointers | Do not replace printed totals with arithmetic or treat total as outstanding balance |
| `lines[]` | Indexed amounts; guarded `line_items` | Preserve order/cardinality including nulls | Each amount pointer | Do not filter missing/invalid lines; structural completeness is not proof that OCR found all lines |
| Issue date | `invoice.issue_date` | Validate extracted ISO date | Date pointer and issues | No date guessing or fallback to today; evaluation date is separate |
| Currency | `invoice.currency` | Validate code | Invoice pointer | No EUR default or inference from location/rule currency |
| Master order amount/currency | `order.total`, `order.currency` | Declared source-format normalization | Master cells, transformation version | No comparison across unknown/different currencies |
| Document type | `invoice.document_type` | Preserve classification | Pointer/evidence | Credit notes/other/unknown do not enter ordinary invoice-payment evaluation; no sign inversion |
| ERP response | `erp.order_payment_status` | Preserve recognized state and order scope | Frozen response, timestamp, record pointer | Unavailable/partial/absent/conflicting is not PENDIENTE; order state is not an invoice payment ledger |
| Revision history | `history.processed_matches` | Match identifiable prior processing records; exclude current revision | Snapshot, record refs, coverage | Processed is not approved/paid; null historical supplier IDs cannot prove identity |
| Approval/payment history | Separate approved/paid matches | Explicit records from designated sources only | Approval/payment record refs | Missing source is unknown, not an empty authoritative ledger |
| Annotations | Referenced review evidence | Preserve kind/text; no authority promotion | Annotation pointer and reading IDs | Unreviewed annotation creates review gate, not executable rule |
| Extraction issues, uncertainties, checks | Quality and blocking findings | Propagate to affected fields and derivations | Issue/check pointers and reading IDs | Relevant uncertainty blocks; unlocalizable problems conservatively block context |
| Additional fields | No automatic binding | Explicit future registered mapping only | Label/raw/normalized pointers | Unknown dependency remains unresolved |

For VAT classification, start with explicitly supported labels and a versioned deterministic mapping. Do not infer VAT solely from percentage, infer a missing tax amount from rate/base, or infer that an absent tax/discount/withholding is zero. A selected VAT subtotal does not by itself establish that the document's total equals base plus VAT.

Comparisons to EUR-denominated tolerances/thresholds require appropriate currency semantics. No currency conversion is introduced here. Legacy amount/date history without currency cannot be made a trustworthy cross-currency match simply by normalizing decimal formatting.

The current declared supplier/order fields contain neither supplier-active status nor order currency. Preserve those gaps until an authoritative source or explicitly approved scoped declaration supplies them.

## 9. Four distinct dimensions of trust

| Dimension | Example | Does not establish |
| --- | --- | --- |
| Observed value | Invoice prints IBAN X | X is approved for payment |
| Source authority | Configured master records IBAN Y | Y was recently reverified or the master is infallible |
| Extraction quality | Valid links and no reported uncertainty | Human verification or factual certainty |
| Review/verification | Named reviewer checked a scoped assertion against named evidence | Authorization of every field or payment execution |

Never label model extraction human verified. Never convert classifier confidence, a checksum, or schema validation into business authorization. Authored rules and untrusted invoice text remain separate trust domains.

A future policy-derived payment destination must be a separate value with separate provenance and authorization, never a replacement of `invoice.iban` or an implicit copy of a new-account note.

## 10. Compatibility and evaluator boundaries

### Existing canonical checks

Keep `(inv, master, params)` temporarily and construct a guarded projection:

- `supplier.id` becomes `inv.vendor_id` only after unique resolution.
- Invoice IBAN stays in `inv.iban`; master IBAN stays in master data.
- Do not pass the new context through the old missing-currency/defaulting and line-dropping normalization path.
- Only project a complete supported line list and semantically valid VAT aggregate.
- Do not call a check when required inputs are unresolved, except deliberate state-inspecting checks such as MISSING.
- Known extraction review conditions remain visible even when legacy checks do not read them.

### Schema 2.0 rules

Leave authored artifacts unchanged and attach bindings alongside them:

- Use merged `id`; preserve `source_refs`, `on_fail`, and original parameter names.
- Freeze exact ruleset bytes/hash and policy identity.
- Use the static alias registry, not fuzzy mapping.
- Allowlist known canonical implementations; never dynamically execute `condition.source`.
- Structured, pending, and unknown implementations remain unsupported until challenge 2.
- Declared but unimplemented semantics, including applicable `require_active` behavior, remain capability blockers. Supplying an active field does not mean the evaluator enforces it.

### Follow-on release blockers, not challenge-1 check rewrites

1. Correct merged-ID handling and account for every enabled rule in dispatch/results.
2. Implement or explicitly reject unsupported conditions and parameter semantics.
3. Apply per-rule `on_fail` and final precedence correctly.
4. Preserve the distinction between processing duplicates and prior payment evidence.

Balanced policy defines PASS → PAGAR, NEEDS_REVIEW → ESCALAR, FAIL → the rule's `on_fail`, with NO_PAGAR > ESCALAR > PAGAR precedence. (Historical note: the pre-implementation `webui/run_revision.py` reducer hardcoded any FAIL to NO_PAGAR; the implemented guarded evaluation path now honors merged IDs, `on_fail`, and precedence, without claiming full policy correctness for every check body.)

The implemented integration prevents a blocked context from appearing as an all-pass approval: unsupported bindings get `execution_status: "unsupported"`, the context reports `"blocked"`, and no recommendation is treated as approval. Generic `run_checks` call sites, canonical check bodies, and new/structured condition kinds remain outside this fix.

### Future seams

Challenge 2 consumes frozen context facts, registry bindings, applicable rules, and capability/readiness information, then emits accountable rule results and policy-correct decisions/reasons.

Challenge 3 receives the structured invoice, resolved master/ERP context, applicable rules, preliminary decision/reasons, and extracted page/block reading evidence. References in this context make that evidence retrievable without reinterpreting raw text as deterministic rule input. The reviewer does not silently promote invoice claims to master authority.

## 11. Implementation surface (delivered)

| File | Delivered change |
| --- | --- |
| `rules_ingestion/decision_context.py` | Types/validation, static field registry, pure construction, dependency preflight, guarded projection |
| `rules_ingestion/decision_registry.py` | Static field/alias registry and check-condition allowlist |
| `rules_ingestion/decision_context.schema.json` | Authoritative machine schema (draft 2020-12) |
| `rules_ingestion/decision_storage.py` | Immutable artifact persistence, local + fail-closed Supabase backends, replay-verified load |
| `rules_ingestion/decision_cli.py` | Offline `python -m rules_ingestion.decision_cli` entrypoint |
| `supabase/migrations/20260919111816_decision_contexts.sql` | Private `ingestion.decision_contexts` index, RLS, no frontend grants |
| `backend/map_invoice.py` | Conservative mapping (no invented VAT/currency) plus `to_decision_context` seam |
| `rules_ingestion/loader.py` | Original row/cell provenance and uncollapsed candidates while preserving legacy consumers |
| `backend/master_data.py` | Explicit source availability/coverage, snapshot metadata, bounded ERP capture |
| `backend/results_store.py` | Decision-context/receipt columns, retained evidence, processed-history snapshot |
| `backend/run_revision.py` | Revision seam and persistence-before-done ordering |
| `tests/decision_context/`, `docs/decision-context-integration.md`, `docs/examples/decision-context/` | Focused tests, integration guide, synthetic runnable inputs |

No UI changes: `frontend/`, `backend/server.py`, and `alberto/` are preserved from `main`
unchanged.

No extraction-schema, provider-prompt, benchmark, canonical-check-body, or profile changes
were made. Persistence scope (private Supabase Storage + `ingestion.decision_contexts`) was
explicitly authorized after this spec was written.

## 12. Acceptance cases

These describe behavior, not an implementation or benchmark harness.

1. **Clean input:** every rule input traces to hashed source bytes and a pointer; derived values retain all contributing inputs.
2. **Reproducibility:** identical source snapshots, adapter/registry versions, ruleset, and evaluation date produce identical semantic facts/bindings/readiness. Run identifiers and capture metadata are not confused with semantic output.
3. **Bank mismatch plus note:** both IBANs and note evidence survive; no master mutation or bank-change authorization is inferred.
4. **Missing currency:** remains null; no EUR appears through a default.
5. **Mixed taxes:** VAT excludes withholding; unsupported reconciliation cannot pass by treating all taxes as IVA.
6. **Partial lines:** null/invalid rows remain visible; no sum over silently filtered lines.
7. **Ambiguous supplier:** multiple normalized-NIF matches or conflicting duplicate IDs produce no selected supplier.
8. **Order conflict:** matching PO text does not bypass conflicting supplier ownership.
9. **ERP unavailable/partial:** cannot become an empty “no paid records” answer or PENDIENTE.
10. **History semantics:** processing is never described as payment; reprocessing does not match itself; missing identity/coverage remains explicit.
11. **Credit note:** retained as a credit note and blocked from ordinary invoice-payment evaluation without an explicit supported policy.
12. **Broken lineage/uncertainty:** relevant dependencies block; invalid hash/reference invalidates readiness. Unknown source-image completeness is not reported as verified completeness.
13. **Unknown field/rule:** remains visible and prevents apparent all-pass.
14. **Unsupported parameter:** capability readiness blocks even if ordinary field inputs exist.
15. **Verification claims:** no human verification or business authorization appears without a scoped explicit record.
16. **Rule identity/policy:** bindings retain schema 2.0 IDs, exact ruleset hash, and per-rule policy metadata without rewriting authored rules.
17. **No silent defaults:** missing dates, amounts, identities, ERP states, and tax rows never acquire passing values through fallback behavior.

Verification was performed offline with synthetic/local fixtures only — no paid providers
or shared databases — against a disposable local Postgres. The implementation is verified
offline; no hosted deployment has been applied.

## 13. Prioritized implementation plan

1. **Freeze the contract and registry.** Approve names, state semantics, transformations, and rule-binding obligations; preserve extraction schema 0.1.
2. **Build the pure adapter and source snapshots.** Retain missing/ambiguous values, original master candidates, source hashes, evidence links, and explicit evaluation date.
3. **Add dependency/capability preflight and guarded projection.** Make incomplete or unsupported work impossible to mistake for all-pass.
4. **Connect the revision seam and verify offline acceptance cases.** Track evaluator correctness as a separate release gate; do not conflate input readiness with payment approval.

Keep the delivered version small and defensible. No general natural-language compilation, universal rule platform, AI reviewer, or payment execution was added. The later-authorized persistence slice (private artifact storage plus the immutable index) is documented in `docs/decision-context-integration.md`.

## 14. Blocking product questions and conservative defaults

No product answer blocks constructing this conservative boundary: unsourced or unsupported facts can remain explicitly blocked.

Before enabling automatic PAGAR for the current balanced workflow:

1. **Which designated source establishes supplier-active status and order currency?** Current declared fields do not. Without a source or approved scoped declaration, affected requirements remain blocked rather than defaulted.
2. **What does “already processed” authorize us to conclude?** May processing alone prohibit payment, or does prohibition require an approval/payment record? Until decided, processing history is processing evidence only.

The implemented version escalates unreviewed annotations conservatively, including potentially benign notes/footers. Improving that distinction belongs to later explicit policy/review work rather than an implicit classifier in the adapter.

## 15. Definition of done and delivery boundary

Challenge 1 is done when an invoice can be joined to frozen rules/master/ERP context with:

- stable meanings and explicit aliases;
- uniquely resolved identities or explicit unresolved states;
- exact monetary/date handling without invented facts;
- field-level provenance through artifacts and reading evidence;
- scoped source authority separate from extraction quality and review;
- explicit source availability, history meaning, and rule dependencies;
- an actionable explanation whenever safe evaluation is not possible;
- a guard against presenting incomplete/unsupported evaluation as all-pass.

After challenge 1 we can reliably inspect **what the evaluator would know, where it came from, and what it cannot conclude**. After the separately scoped evaluator fixes, that foundation can support policy-correct PAGAR/ESCALAR/NO_PAGAR decisions. After the later reviewer work, it can also support a second-pass review grounded in the same frozen evidence.

The documented contract is implemented and verified offline. The hosted migration has not
been applied; remaining limitations (unsupported rule semantics, processed-vs-paid history
policy, reviewer work) are listed in `docs/decision-context-integration.md`.
