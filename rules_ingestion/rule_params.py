"""Read a canonical rule's PARAMETERS off the norm sheet.

Classification answers *which* rule a norm line is about. It never answers
*with what number*, so a sheet saying "facturas por encima de 5.000 EUR
requieren aprobacion" used to activate the AUTHORIZATION rule while the
threshold stayed at whatever the profile had -- the sheet was provenance, the
profile was authority.

This module closes that gap for the parameters the executor actually
implements. Extraction is deterministic (no model call): the expensive part,
routing a sentence to a rule family, already happened in classify.py.

Three deliberate limits:

  * only parameters in SHEET_SETTABLE can be set, because
    execution_rules.plan_rule refuses anything else and the rule would become
    UNSUPPORTED rather than stricter.
  * every value is type-checked here, so a malformed sheet cannot smuggle a
    string into a threshold.
  * two lines that disagree about the same parameter set nothing and record a
    conflict. Silently picking one reading of a contradictory norm is how a
    payment limit moves without anyone deciding to move it.
"""
from __future__ import annotations

import re

from .condition_authoring import AuthoringError, decimal_text
from .normalize import _accent_fold

# Parameters a norm line may set, per canonical rule. Restricted to what
# execution_rules implements: DUPLICATES' keys and soft verdict are fixed by
# the planner, so they are absent on purpose.
SHEET_SETTABLE = {
    "VENDOR": ("require_iban_match", "require_nif_in_master", "require_active",
               "check_nif_control_digit"),
    "DUPLICATES": ("require_erp_pending",),
    "AMOUNT": ("tolerance_eur", "allowed_currencies", "check_line_items_sum",
               "check_total_is_base_plus_iva", "check_matches_pedido", "check_iva"),
    "AUTHORIZATION": ("escalate_above_eur",),
    "DATES": ("allow_future", "enforce_payment_terms"),
    "MISSING": ("required_fields",),
}

# Words a norm line uses for a document field -> the name the planner resolves.
_REQUIRED_FIELD_WORDS = (
    (("nif", "cif"), "nif"),
    (("iban", "cuenta bancaria"), "iban"),
    (("pedido", "numero de pedido"), "pedido"),
    (("importe", "total"), "importe"),
    (("iva",), "iva"),
    (("fecha",), "fecha"),
)

_AMOUNT = r"([\d][\d.,\s]*)"
_ABOVE = (r"(?:supere[n]?|superior(?:es)?\s+a|por\s+encima\s+de|mayor(?:es)?\s+"
          r"(?:que|a)|mas\s+de|a\s+partir\s+de)")
_APPROVAL = ("aprobacion", "autoriz", "responsable", "firma", "visto bueno",
             "director", "escalar", "revision")


def _amount(text: str) -> str | None:
    match = re.search(_ABOVE + r"\s*(?:los\s+)?" + _AMOUNT, text)
    if not match:
        return None
    try:
        return decimal_text(match.group(1))
    except AuthoringError:
        return None


def _authorization(text: str) -> dict:
    if not any(word in text for word in _APPROVAL):
        return {}
    amount = _amount(text)
    return {"escalate_above_eur": amount} if amount is not None else {}


def _amount_params(text: str) -> dict:
    found: dict = {}
    match = re.search(r"(?:toleranci\w*|margen|desviacion)\s*(?:de\s+)?(?:hasta\s+)?"
                      + _AMOUNT, text)
    if match:
        try:
            found["tolerance_eur"] = decimal_text(match.group(1))
        except AuthoringError:
            pass
    if "euro" in text and any(word in text for word in
                              ("solo", "unicamente", "exclusivamente")):
        found["allowed_currencies"] = ["EUR"]
    if any(word in text for word in ("suma de las lineas", "lineas de detalle",
                                     "suma de los conceptos")):
        found["check_line_items_sum"] = True
    if "base" in text and "iva" in text and any(
            word in text for word in ("total", "suma", "cuadr")):
        found["check_total_is_base_plus_iva"] = True
    if "pedido" in text and any(word in text for word in
                                ("coincid", "cuadr", "corresponde", "igual")):
        found["check_matches_pedido"] = True
    return found


def _vendor(text: str) -> dict:
    found: dict = {}
    matches = any(word in text for word in ("coincid", "maestro", "mismo", "igual"))
    if "iban" in text and matches:
        found["require_iban_match"] = True
    if ("nif" in text or "cif" in text) and matches:
        found["require_nif_in_master"] = True
    if "activo" in text and "proveedor" in text:
        found["require_active"] = True
    if any(word in text for word in ("digito de control", "letra del nif",
                                     "digito de control del nif", "nif valido")):
        found["check_nif_control_digit"] = True
    return found


def _dates(text: str) -> dict:
    found: dict = {}
    if "futur" in text:
        found["allow_future"] = False
    if any(word in text for word in ("plazo", "vencimiento", "condiciones de pago",
                                     "dias desde")):
        found["enforce_payment_terms"] = True
    return found


def _duplicates(text: str) -> dict:
    if "pendiente" in text or "erp" in text:
        return {"require_erp_pending": True}
    return {}


def _missing(text: str) -> dict:
    if not any(word in text for word in ("debe indicar", "debe llevar", "debe incluir",
                                         "obligatori", "debe constar", "debe figurar")):
        return {}
    names = [name for words, name in _REQUIRED_FIELD_WORDS
             if any(word in text for word in words)]
    return {"required_fields": names} if names else {}


_EXTRACTORS = {
    "VENDOR": _vendor,
    "DUPLICATES": _duplicates,
    "AMOUNT": _amount_params,
    "AUTHORIZATION": _authorization,
    "DATES": _dates,
    "MISSING": _missing,
}


def _valid(canonical: str, param: str, value: object) -> bool:
    if param not in SHEET_SETTABLE.get(canonical, ()):
        return False
    if param in ("tolerance_eur", "escalate_above_eur"):
        return isinstance(value, str) and not value.startswith("-")
    if param in ("allowed_currencies", "required_fields"):
        return (isinstance(value, list) and bool(value)
                and all(isinstance(item, str) and item for item in value))
    return isinstance(value, bool)


def extract(canonical: str, text: str) -> dict:
    """Parameters one norm line states for `canonical`. Never raises."""
    extractor = _EXTRACTORS.get(canonical)
    if extractor is None or not isinstance(text, str) or not text.strip():
        return {}
    folded = _accent_fold(text).lower()
    return {param: value for param, value in (extractor(folded) or {}).items()
            if _valid(canonical, param, value)}


def from_sheet(canonical: str, matched: list) -> tuple[list[dict], list[dict]]:
    """Read every matched norm line for one rule.

    Returns (settings, conflicts). A setting carries its source cell so the
    ruleset records which line moved the parameter; a conflict records the
    readings that disagreed and leaves the profile value in place.
    """
    readings: dict[str, list[dict]] = {}
    for line in matched or []:
        if not isinstance(line, dict):
            continue
        for param, value in extract(canonical, line.get("text") or "").items():
            readings.setdefault(param, []).append({
                "param": param, "value": value,
                "cell": line.get("cell"), "sheet": line.get("sheet"),
                "text": line.get("text"),
            })

    settings, conflicts = [], []
    for param, found in readings.items():
        distinct = {repr(entry["value"]) for entry in found}
        if len(distinct) > 1:
            conflicts.append({"param": param, "readings": found})
            continue
        settings.append(found[0])
    return settings, conflicts
