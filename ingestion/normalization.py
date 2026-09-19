"""Conservative, deterministic normalization for invoice scalar values."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

_SPACE_RE = re.compile(r"[\s\u00a0\u202f]+")
_MONTHS = {
    "enero": 1,
    "ene": 1,
    "febrero": 2,
    "feb": 2,
    "marzo": 3,
    "mar": 3,
    "abril": 4,
    "abr": 4,
    "mayo": 5,
    "may": 5,
    "junio": 6,
    "jun": 6,
    "julio": 7,
    "jul": 7,
    "agosto": 8,
    "ago": 8,
    "septiembre": 9,
    "setiembre": 9,
    "sep": 9,
    "sept": 9,
    "octubre": 10,
    "oct": 10,
    "noviembre": 11,
    "nov": 11,
    "diciembre": 12,
    "dic": 12,
}


def _plain(value: Any) -> str | None:
    if value is None:
        return None
    text = _SPACE_RE.sub(" ", str(value)).strip()
    return text or None


def normalize_decimal(value: Any) -> str | None:
    """Normalize an explicitly written number to a JSON-schema decimal string.

    Separators are interpreted from their written form.  A single comma is
    treated as a decimal separator; values with both separators use the last
    separator as the decimal separator.  No missing quantity or currency is
    invented and malformed/ambiguous values return ``None``.
    """

    text = _plain(value)
    if text is None:
        return None
    text = text.replace("−", "-").replace("–", "-")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    # Currency symbols and a trailing percent sign are presentation, not part
    # of the numeric value.  Letters otherwise make the value unsafe to infer.
    text = re.sub(r"^[€$£]|[€$£%]$", "", text).strip()
    # Spaces/NBSPs are commonly used as thousands grouping in Spanish PDFs;
    # removing them preserves the printed digits without guessing a value.
    text = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"[+-]?[\d.,]+", text):
        return None
    if text.count(",") and text.count("."):
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in text:
        # A comma is the conventional decimal marker in Spanish invoices.
        text = text.replace(",", ".")
    # Multiple dots can only be grouping if every trailing group has 3 digits.
    elif text.count(".") > 1:
        parts = text.split(".")
        if not all(len(part) == 3 for part in parts[1:]):
            return None
        text = "".join(parts)
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if negative:
        number = -number
    if not number.is_finite():
        return None
    result = format(number, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    if result in {"", "-0"}:
        result = "0"
    return result


def normalize_quantity(value: Any) -> str | None:
    return normalize_decimal(value)


def normalize_amount(value: Any) -> str | None:
    return normalize_decimal(value)


def normalize_percent(value: Any) -> str | None:
    return normalize_decimal(value)


def normalize_date(value: Any) -> str | None:
    """Return ISO ``YYYY-MM-DD`` only when the input identifies one date."""

    text = _plain(value)
    if text is None:
        return None
    # ISO dates are unambiguous, including timestamps whose date is explicit.
    candidate = text[:10]
    try:
        parsed = date.fromisoformat(candidate)
        if len(text) == 10 or text[10:11] in {"T", " "}:
            return parsed.isoformat()
    except ValueError:
        pass

    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(ch for ch in folded if unicodedata.category(ch) != "Mn")
    folded = re.sub(r"\bde\b", " ", folded)
    month_pattern = "|".join(sorted(_MONTHS, key=len, reverse=True))
    match = re.fullmatch(rf"(\d{{1,2}})\s+({month_pattern})\.?\s+(\d{{4}})", folded)
    if not match:
        match = re.fullmatch(
            rf"(\d{{1,2}})\s+({month_pattern})\s*,?\s*(\d{{4}})", folded
        )
    if match:
        day, month, year = (
            int(match.group(1)),
            _MONTHS[match.group(2)],
            int(match.group(3)),
        )
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None

    match = re.fullmatch(r"(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4})", folded)
    if not match:
        return None
    first, second, third = map(int, match.groups())
    if len(match.group(1)) == 4:
        year, month, day = first, second, third
    elif len(match.group(3)) == 4:
        year = third
        # Only accept a day/month ordering when it is unambiguous.
        if first > 12 and second <= 12:
            day, month = first, second
        elif second > 12 and first <= 12:
            day, month = second, first
        else:
            return None
    else:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def normalize_iban(value: Any) -> str | None:
    text = _plain(value)
    if text is None:
        return None
    # The contract asks only for whitespace removal.  Keep even a malformed or
    # partially read identifier visible for review rather than discarding it.
    return re.sub(r"\s+", "", text).upper()


def normalize_invoice(invoice: dict) -> dict:
    """Normalize only explicitly supplied scalars; retain uncertain raw values as issues."""
    import copy

    result = copy.deepcopy(invoice)
    # Do not repair missing keys or invent rows: the canonical schema still validates them.
    if "issues" not in result or not isinstance(result["issues"], list):
        return result

    def apply(container, key, pointer, normalizer):
        if (
            not isinstance(container, dict)
            or key not in container
            or container[key] is None
        ):
            return
        raw = container[key]
        value = normalizer(raw)
        container[key] = value
        if value is None:
            result["issues"].append(
                {
                    "field": pointer,
                    "kind": "ambiguous",
                    "raw_text": str(raw),
                    "candidates": [],
                }
            )

    apply(result, "issue_date", "/issue_date", normalize_date)
    apply(result.get("payment"), "iban", "/payment/iban", normalize_iban)
    for key in ("taxable_base", "total"):
        apply(result.get("totals"), key, "/totals/" + key, normalize_decimal)
    for collection, fields in (
        ("lines", ("quantity", "amount")),
        ("taxes", ("rate_percent", "amount")),
    ):
        if isinstance(result.get(collection), list):
            for index, row in enumerate(result[collection]):
                for key in fields:
                    apply(row, key, f"/{collection}/{index}/{key}", normalize_decimal)
    return result
