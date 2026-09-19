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
        title="Proveedor válido y cuenta que coincide",
        description=(
            "El proveedor existe en el maestro y está activo; el NIF está en el "
            "maestro; el IBAN de la factura coincide con el del maestro. "
            "IBAN distinto -> señal de fraude -> ESCALAR."
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
        title="Sin pagos duplicados",
        description=(
            "Misma factura + mismo proveedor -> NO_PAGAR (ya pagado / doble "
            "envío). Mismo importe + misma fecha -> NEEDS_REVIEW. El estado ERP "
            "del pedido debe ser PENDIENTE; PAGADA -> ya pagado -> NO_PAGAR."
        ),
        on_fail="NO_PAGAR",
        default_enabled=True,
        input_fields=("invoice_number", "vendor_id", "amount", "date", "pedido"),
        lexicon=(
            "duplicado", "duplicada", "dos", "veces", "doble", "mismo", "misma",
            "pagar", "pagada", "pagado", "pendiente", "estado", "repetir",
            "reintegro", "factura", "ya",
        ),
        soft_verdict="NEEDS_REVIEW",
        notes="Hard key (invoice+vendor) NO_PAGAR; soft key (amount+date) review.",
    ),
    "AMOUNT": CanonicalRule(
        key="AMOUNT",
        rule_id="R3",
        title="Importes e IVA cuadran",
        description=(
            "Las líneas suman la base; el IVA está bien calculado; el total = "
            "base + IVA (±0,01); moneda EUR; y el importe de la factura coincide "
            "con el del pedido (±0,01)."
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
        title="Autorización por importe (umbral)",
        description=(
            "Importe por encima de un umbral -> requiere aprobación humana "
            "(ESCALAR). Adición nuestra; OFF en el perfil de la norma v3."
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
        title="Fechas válidas y en plazo",
        description=(
            "La fecha es válida y no futura; y está dentro del plazo de pago del "
            "proveedor (Condiciones, p.ej. '60 dias')."
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
        title="Campos obligatorios presentes",
        description=(
            "Faltan datos obligatorios (NIF / IBAN / PEDIDO / IMPORTE / IVA / "
            "FECHA) -> ESCALAR para que un humano los complete."
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
