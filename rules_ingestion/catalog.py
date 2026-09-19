"""catalog.py · the 6 canonical payment rules (the `predefined` source).

These are OUR rules. They double as:
  * the `predefined` half of the merged ruleset, and
  * the target classes for JEV's Choice(maps_to): a free-text norm line is
    routed to exactly one of these keys (or NONE).

Each rule carries a `lexicon` (accent-folded Spanish tokens). classify.py scores
a norm line against every lexicon; the best-scoring rule wins maps_to. Keeping
the vocabulary HERE (next to the rule it defines) means adding/curating a rule
never means touching the classifier.

on_fail semantics (consumed by the decision layer, precedence NO_PAGAR>ESCALAR>PAGAR):
  * NO_PAGAR  : hard stop, do not pay (fraud-certain / already-paid)
  * ESCALAR   : a human must look (fraud-signal / anomaly / missing data)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Canonical decision vocabulary. NONE = "not one of our rules".
CHOICE_LABELS: Tuple[str, ...] = (
    "VENDOR", "DUPLICATES", "AMOUNT", "AUTHORIZATION", "DATES", "MISSING", "NONE",
)


@dataclass(frozen=True)
class CanonicalRule:
    key: str                      # VENDOR
    rule_id: str                  # R1
    title: str
    description: str
    on_fail: str                  # NO_PAGAR | ESCALAR
    default_enabled: bool
    input_fields: Tuple[str, ...] # invoice fields the decision layer will read
    lexicon: Tuple[str, ...]      # accent-folded tokens for Choice(maps_to)
    notes: str = ""
    soft_verdict: Optional[str] = None   # NEEDS_REVIEW branch, if any


CATALOG: Dict[str, CanonicalRule] = {
    "VENDOR": CanonicalRule(
        key="VENDOR",
        rule_id="R1",
        title="Valid vendor and matching account",
        description=(
            "The vendor exists in the master and is active; the NIF is in the "
            "master; the invoice IBAN matches the master's. "
            "Different IBAN -> fraud signal -> ESCALAR."
        ),
        on_fail="ESCALAR",
        default_enabled=True,
        input_fields=("vendor_id", "nif", "iban"),
        lexicon=(
            "nif", "iban", "maestro", "proveedor", "coincida", "coincide",
            "cuenta", "titular", "banco", "activo", "razon", "social", "fraude",
        ),
        notes="IBAN mismatch is the marzo-incident fraud signal.",
    ),
    "DUPLICATES": CanonicalRule(
        key="DUPLICATES",
        rule_id="R2",
        title="No duplicate payments",
        description=(
            "Same invoice + same vendor -> NO_PAGAR (already paid / double "
            "submit). Same amount + same date -> NEEDS_REVIEW. The ERP state "
            "of the order must be PENDIENTE; PAGADA -> already paid -> NO_PAGAR."
        ),
        on_fail="NO_PAGAR",
        default_enabled=True,
        input_fields=("invoice_number", "vendor_id", "amount", "date", "pedido"),
        lexicon=(
            "duplicado", "duplicada", "dos", "veces", "doble", "mismo", "misma",
            "pagada", "pagado", "pendiente", "estado", "repetir",
            "reintegro", "ya",
        ),
        soft_verdict="NEEDS_REVIEW",
        notes="Hard key (invoice+vendor) NO_PAGAR; soft key (amount+date) review.",
    ),
    "AMOUNT": CanonicalRule(
        key="AMOUNT",
        rule_id="R3",
        title="Amounts and VAT add up",
        description=(
            "Line items sum to the base; VAT is correctly computed; total = "
            "base + VAT (±0.01); currency EUR; and the invoice amount matches "
            "the purchase-order amount (±0.01)."
        ),
        on_fail="ESCALAR",
        default_enabled=True,
        input_fields=("line_items", "base", "iva", "total", "currency", "pedido"),
        lexicon=(
            "importe", "iva", "total", "base", "calculo", "calculado",
            "tolerancia", "euro", "eur", "moneda", "suma", "lineas", "pedido",
            "igual", "cantidad", "impuesto",
        ),
        notes="Absorbs norm sentences 2 (pedido/importe) and 3 (IVA/total).",
    ),
    "AUTHORIZATION": CanonicalRule(
        key="AUTHORIZATION",
        rule_id="R4",
        title="Amount-based authorization (threshold)",
        description=(
            "Amount above a threshold -> requires human approval "
            "(ESCALAR). Our addition; OFF in the v3 norm profile."
        ),
        on_fail="ESCALAR",
        default_enabled=False,      # OFF in the norm profile
        input_fields=("total",),
        lexicon=(
            "autorizacion", "autorizar", "aprobar", "aprobacion", "umbral",
            "limite", "superior", "mayor", "supere", "firma", "responsable",
        ),
        notes="Not present in norm v3 text; our addition, disabled by profile.",
    ),
    "DATES": CanonicalRule(
        key="DATES",
        rule_id="R5",
        title="Valid dates within payment terms",
        description=(
            "The date is valid and not in the future; and it falls within the "
            "vendor's payment terms (Condiciones, e.g. '60 dias')."
        ),
        on_fail="ESCALAR",
        default_enabled=True,
        input_fields=("date", "vendor_id"),
        lexicon=(
            "fecha", "futura", "futuro", "valida", "valido", "vencimiento",
            "plazo", "dias", "vencida", "caducada", "emision", "temporal",
        ),
        notes="Payment-term window derived from Proveedores.condiciones.",
    ),
    "MISSING": CanonicalRule(
        key="MISSING",
        rule_id="R6",
        title="Required fields present",
        description=(
            "Missing required data (NIF / IBAN / PEDIDO / IMPORTE / IVA / "
            "FECHA) -> ESCALAR so a human can complete them."
        ),
        on_fail="ESCALAR",
        default_enabled=True,
        input_fields=("nif", "iban", "pedido", "importe", "iva", "fecha"),
        lexicon=(
            "falta", "faltan", "campo", "campos", "obligatorio", "obligatorios",
            "ausente", "vacio", "incompleto", "sin", "requerido", "completar",
            "anomalia", "humano", "escalar", "duda", "revisar", "motivo",
        ),
        notes="Also the catch-all for norm sentence 6 (escalar ante duda).",
    ),
}


def rule_by_id(rule_id: str) -> Optional[CanonicalRule]:
    for r in CATALOG.values():
        if r.rule_id == rule_id:
            return r
    return None


def all_lexicons() -> Dict[str, Tuple[str, ...]]:
    """key -> lexicon, for the classifier. Excludes NONE (it has no lexicon)."""
    return {k: v.lexicon for k, v in CATALOG.items()}


def lexicon_grounded(text: str, canonical: str) -> bool:
    """True iff at least one catalog lexicon token appears in the source text.

    Used to reject DeepSeek (and merge) activations that claim a canonical
    without any overlapping vocabulary — the IRPF→AMOUNT failure mode.
    """
    from .normalize import _accent_fold
    canon = CATALOG.get(canonical)
    if canon is None or not text:
        return False
    folded = _accent_fold(text)
    return any(tok in folded for tok in canon.lexicon)


def catalog_brief() -> str:
    """Short label+description block for LLM prompts (grounding)."""
    lines = []
    for key, r in CATALOG.items():
        lines.append(f"- {key}: {r.title}. {r.description}")
    lines.append("- NONE: the line is a payment rule but matches no canonical.")
    return "\n".join(lines)
