"""normalize.py · deterministic normalizers for the Caja's messy inputs.

Everything that has to *match* across sources (factura vs master vs ERP) passes
through here first. If matching fails, it is almost always because two sides were
never normalized to the same shape. This module is that shape.

Design rules:
  * Pure functions, no I/O, no globals. Same input -> same output.
  * None / blank in -> None out (never raise on missing data).
  * Money is Decimal, never float (float can't represent 12.874,40 exactly).

Verified gotchas from La Caja:
  * IBANs stored space-formatted:            'ES21 0049 1500 ...'
  * Vendor names with trailing whitespace:   'Ofimática Cieza S.L.  '
  * ES-format money with thousands + comma:   12.874,40
  * ERP dates are DD/MM/AAAA;  Excel dates are ISO or datetime objects
  * Synthetic CIFs mostly FAIL the real control digit -> it is advisory only,
    master membership + IBAN match are the hard gates.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Default money-comparison tolerance from the norm ("tolerancia 0,01 EUR").
TOLERANCE_EUR = Decimal("0.01")

Number = Union[int, float, str, Decimal]


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
def norm_text(value: Optional[object]) -> Optional[str]:
    """Strip ends and collapse internal whitespace runs to a single space.

    'Ofimática Cieza S.L.  '  -> 'Ofimática Cieza S.L.'
    """
    if value is None:
        return None
    s = str(value).replace(" ", " ")  # non-breaking space -> space
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def _accent_fold(s: str) -> str:
    """Lowercase + drop diacritics. For fuzzy comparison only, never storage."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# --------------------------------------------------------------------------- #
# IBAN
# --------------------------------------------------------------------------- #
def norm_iban(value: Optional[object]) -> Optional[str]:
    """Strip all whitespace and uppercase. 'ES21 0049 ...' -> 'ES210049...'."""
    if value is None:
        return None
    s = re.sub(r"\s+", "", str(value)).upper()
    return s or None


def iban_is_valid(value: Optional[object]) -> bool:
    """ISO 7064 mod-97 checksum on a normalized IBAN. Structural, not existence."""
    iban = norm_iban(value)
    if not iban or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", iban) or len(iban) < 5:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)  # A->10 ... Z->35
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# NIF / CIF / NIE
# --------------------------------------------------------------------------- #
_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETTERS = "JABCDEFGHI"


def norm_nif(value: Optional[object]) -> Optional[str]:
    """Uppercase, drop spaces/dots/hyphens. 'b-46.102.331' -> 'B46102331'."""
    if value is None:
        return None
    s = re.sub(r"[\s.\-]", "", str(value)).upper()
    return s or None


def nif_control_ok(value: Optional[object]) -> bool:
    """Validate the control character of a Spanish DNI / NIE / CIF.

    ADVISORY. La Caja's synthetic master NIFs mostly fail this, so rules must
    key on master membership, not on this. Kept accurate for real production
    data (post-hackathon) and to surface genuinely malformed identifiers.
    """
    nif = norm_nif(value)
    if not nif:
        return False
    # DNI: 8 digits + letter
    if re.fullmatch(r"\d{8}[A-Z]", nif):
        return nif[-1] == _DNI_LETTERS[int(nif[:8]) % 23]
    # NIE: [XYZ] + 7 digits + letter  (X->0, Y->1, Z->2)
    if re.fullmatch(r"[XYZ]\d{7}[A-Z]", nif):
        prefix = {"X": "0", "Y": "1", "Z": "2"}[nif[0]]
        return nif[-1] == _DNI_LETTERS[int(prefix + nif[1:8]) % 23]
    # CIF: org letter + 7 digits + control (digit or letter)
    if re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", nif):
        digits = nif[1:8]
        total = 0
        for i, ch in enumerate(digits):
            n = int(ch)
            if i % 2 == 0:          # odd position (1st,3rd,...) -> double
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        control_digit = (10 - (total % 10)) % 10
        control = nif[-1]
        return control == str(control_digit) or control == _CIF_LETTERS[control_digit]
    return False


def nif_looks_wellformed(value: Optional[object]) -> bool:
    """Format-only structural check (no control digit). Cheap sanity gate."""
    nif = norm_nif(value)
    if not nif:
        return False
    return bool(
        re.fullmatch(r"\d{8}[A-Z]", nif)
        or re.fullmatch(r"[XYZ]\d{7}[A-Z]", nif)
        or re.fullmatch(r"[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]", nif)
    )


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #
def norm_importe(value: Optional[Number]) -> Optional[Decimal]:
    """Parse money to Decimal, tolerant of ES and EN formats and a currency tag.

        '12.874,40'      -> Decimal('12874.40')
        '12,874.40 EUR'  -> Decimal('12874.40')
        1234.5 (float)   -> Decimal('1234.50')
        '-0,00'          -> Decimal('0.00')
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)   # drop currency symbols / letters / spaces
    if not s or s in {"-", ".", ","}:
        return None
    if "," in s and "." in s:
        # Both present: the LAST separator is the decimal one.
        if s.rfind(",") > s.rfind("."):     # ES: 12.874,40
            s = s.replace(".", "").replace(",", ".")
        else:                                # EN: 12,874.40
            s = s.replace(",", "")
    elif "," in s:
        # Comma only -> decimal comma (ES). '1234,56' -> '1234.56'
        s = s.replace(",", ".")
    # dot-only or plain digits: already fine
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def amounts_equal(a: Optional[Number], b: Optional[Number],
                  tol: Decimal = TOLERANCE_EUR) -> bool:
    """True if |a - b| <= tol. Missing on either side -> False."""
    da, db = norm_importe(a), norm_importe(b)
    if da is None or db is None:
        return False
    return abs(da - db) <= tol


# --------------------------------------------------------------------------- #
# Dates
# --------------------------------------------------------------------------- #
def norm_fecha(value: Optional[object]) -> Optional[str]:
    """Normalize to ISO 'AAAA-MM-DD'. Accepts DD/MM/AAAA, ISO, datetime/date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def is_future(iso_date: Optional[str], today: Optional[str] = None) -> bool:
    """True if iso_date is strictly after `today` (ISO). Unparseable -> False."""
    d = norm_fecha(iso_date)
    if d is None:
        return False
    ref = norm_fecha(today) if today else datetime.now().date().isoformat()
    return d > (ref or datetime.now().date().isoformat())


def payment_terms_days(condiciones: Optional[str]) -> Optional[int]:
    """Extract the integer day count from '60 dias' -> 60."""
    if condiciones is None:
        return None
    m = re.search(r"\d+", str(condiciones))
    return int(m.group()) if m else None
