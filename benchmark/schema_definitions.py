"""Source of the checked-in JSON Schemas. Run python -m benchmark.schema_definitions."""
import json
from pathlib import Path

S = {"type": "string"}
N = {"type": ["string", "null"]}
D = {"type": ["string", "null"], "pattern": r"^-?\d+(\.\d+)?$"}
I = {"type": "integer", "minimum": 1}
HASH = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
def enum(*values): return {"enum": list(values)}
def arr(item): return {"type": "array", "items": item}
def obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

ISSUE = obj(field={"type": "string", "pattern": "^(/.*)?$"}, kind=enum("unreadable", "ambiguous", "conflicting"), raw_text=N, candidates=arr(S))
PARTY = obj(name=N, tax_id=N, location=N, address=N)
INVOICE = obj(schema_version={"const": "0.1"}, file_id=S,
    document_type=enum("invoice", "credit_note", "other", "unknown"),
    invoice_number=N, issue_date={"type": ["string", "null"], "format": "date"},
    purchase_order_reference=N, currency=N, supplier=PARTY, customer=PARTY,
    payment=obj(iban={"type": ["string", "null"], "pattern": r"^\S+$"}),
    lines=arr(obj(position=I, description=S, quantity=D, amount=D)),
    taxes=arr(obj(label=S, rate_percent=D, amount=D)),
    totals=obj(taxable_base=D, total=D),
    annotations=arr(obj(kind=enum("note", "stamp", "footer", "other"), text=S)),
    additional_fields=arr(obj(label=S, raw_value=S, normalized_value=N)), issues=arr(ISSUE))
CELL = obj(id=S, column=I, row_span=I, column_span=I, text=S)
BLOCK = obj(id=S, kind=enum("heading", "paragraph", "table", "note", "stamp", "footer", "other"),
    text=S, rows=arr(obj(id=S, cells=arr(CELL))),
    uncertainties=arr(obj(kind=enum("unreadable", "uncertain"), raw_text=N, description=S)))
READING = obj(schema_version={"const": "0.1"}, file_id=S,
    capabilities=obj(block_kinds={"type": "boolean"}, tables={"type": "boolean"}, layout={"type": "boolean"}),
    pages=arr(obj(page=I, blocks=arr(BLOCK), non_text_elements=arr(obj(description=S, reference_ids=arr(S))))))
ENTRY = obj(file_id=S, path=S, sha256=HASH, split=enum("development", "held_out"))
MANIFEST = obj(schema_version={"const": "0.1"}, selection_status=enum("awaiting_user_selection", "selected", "synthetic_fixture"), selection_source=N, documents=arr(ENTRY))
LINK = obj(page=I, reference_ids=arr(S))
REVIEW = obj(author=S, source=enum("codex_visual", "human_visual", "synthetic_fixture"),
    status=enum("draft", "human_verified"), reviewer=N, reviewed_at=N, artifact_sha256=HASH,
    complete={"type": "boolean"}, inspected_pages=arr(I), excluded_pointers=arr(S))
PROVENANCE = obj(schema_version={"const": "0.1"}, file_id=S, source_sha256=HASH,
    stage2=REVIEW, stage3=REVIEW,
    pointers={"type": "object", "additionalProperties": arr(LINK)}, unresolved=arr(S))
RUN = obj(schema_version={"const": "0.1"}, run_id=S, track=enum("ocr", "isolated", "end_to_end"),
    provider=S, model=S, prompt_version=S, config_hash=HASH, manifest_hash=HASH,
    split=enum("development", "held_out", "all"), reading_run=N, provisional={"type": "boolean"},
    created_at=S, config={"type": "object"}, documents=arr(S))
RECORD = obj(schema_version={"const": "0.1"}, file_id=S, source_sha256=HASH, reading_sha256={"anyOf": [HASH, {"type": "null"}]},
    status=enum("pending", "success", "invalid", "failed", "blocked"), provider=S, model=S, resolved_model=N,
    prompt_version=S, latency_seconds={"type": ["number", "null"], "minimum": 0},
    usage={"type": "object"}, cost_usd=D, attempts={"type": "integer", "minimum": 0},
    error=N, artifact_sha256={"anyOf": [HASH, {"type": "null"}]}, imported={"type": "boolean"})
SCHEMAS = dict(invoice=INVOICE, reading=READING, manifest=MANIFEST, provenance=PROVENANCE, run=RUN, record=RECORD)
if __name__ == "__main__":
    for name, schema in SCHEMAS.items():
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": name, **schema}
        (Path(__file__).parent / "schemas" / f"{name}.json").write_text(json.dumps(schema, indent=2) + "\n")
