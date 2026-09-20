"""Deterministic first-pass field extraction over the PDF text layer.

Ported from the ``alberto/extraccion`` cascade removed on main: labels are
anchored at the start of a line (after an optional dash/bullet), a label
inside another word never matches, the first number after a label is the
value (a number followed by '%' is a rate, not an amount), and DD/MM/YYYY
follows the Spanish day-first convention used across this corpus.  Nothing
is inferred: a field that cannot be anchored stays null and shows up in
``gaps`` so the vision stage can take over.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

MIN_TEXT_CHARS = 20
VERSION = "deterministic-fields/1"

GATE_POINTERS = (
    "/invoice_number",
    "/issue_date",
    "/purchase_order_reference",
    "/supplier/name",
    "/supplier/tax_id",
    "/payment/iban",
    "/totals/taxable_base",
    "/totals/total",
    "/taxes/0/amount",
)

RE_PO = re.compile(r"\bPO-\d{4}-\d{4}\b")
# IBAN en su forma internacional (ISO 13616). Antes el patron solo aceptaba ES
# y el lote 2 trae proveedores aleman, frances, brasileno y japones: sus IBAN
# venian impresos y se perdian.
#
# NO se valida el mod-97. Se probo, y los 11 IBAN del maestro de La Caja lo
# fallan (son sinteticos), igual que 3 de los 4 nuevos: filtrar por checksum
# habria tirado casi todos. Lo que si es fiable es la ETIQUETA, presente en
# 120 de 120 facturas del lote 1 y en 39 de 40 del lote 2, asi que el valor se
# ancla ahi, igual que el identificador fiscal.
RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,6})+")
RE_IBAN_LABEL = re.compile(r"\biban\b[\s):.\]-]*")
RE_NIF = re.compile(r"\b(?:[A-Z]\d{8}|\d{8}[A-Z])\b")
# El identificador fiscal se ancla en SU ETIQUETA, no en la forma espanola:
# "NIF", "CIF", "VAT", "USt-ID", "N° TVA", "CNPJ", "Tax ID". El valor va detras
# y cada pais lo escribe a su manera (DE812345678, 12.345.678/0001-95,
# 5010401075570), asi que el patron del valor es de forma, no de pais.
# El separador admite parentesis y puntuacion porque La Caja escribe tanto
# "NIF: X" como "CUENTA DE ABONO (IBAN): X".
RE_TAX_LABEL = re.compile(
    r"\b(?:nif|cif|vat|ust-?\s?id(?:-?nr)?|tva|cnpj|tax\s*id|p\.?\s*iva)\b"
    r"[\s):.\]-]*")
RE_TAXID_VALUE = re.compile(r"[A-Z0-9][A-Z0-9.\-/]{5,19}")
# DD/MM/YYYY is the Spanish convention of this corpus, not MM/DD.
RE_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
RE_DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
RE_DATE_LONG = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zñáéíóú]+)\s+de\s+(\d{4})\b", re.IGNORECASE)
RE_NUM = re.compile(r"-?\d[\d.,]*")
RE_PCT = re.compile(r"\s*%")
RE_TOKEN = re.compile(r"[A-Z0-9][A-Z0-9/\-]{2,}")
RE_DOCTYPE = re.compile(r"\b(?:factura|invoice)\b", re.IGNORECASE)
RE_CURRENCY = re.compile(
    r"US\$|\b(?:EUR|USD|GBP|CHF|MXN|ARS|COP)\b|[€$£]")
CURRENCY_CODES = {
    "€": "EUR", "EUR": "EUR",
    "$": "USD", "US$": "USD", "USD": "USD",
    "£": "GBP", "GBP": "GBP",
    "CHF": "CHF", "MXN": "MXN", "ARS": "ARS", "COP": "COP",
}
RE_LEGAL = re.compile(
    r"\b(?:S\.?\s?L\.?\s?U?\.?|S\.?\s?A\.?|S\.?\s?C\.?|S\.?\s?COOP\.?|C\.?\s?B\.?)",
    re.IGNORECASE)
RE_LEAD = re.compile(r"^\s*[-·•]*\s*")
# `_flat` quita acentos y baja a minusculas, asi que aqui van ya plegados:
# "Facture a" (fr), "Rechnungsempfanger" (de), "Faturar a" (pt).
RE_CUSTOMER = re.compile(
    r"^\s*[-·•]?\s*(cliente|bill to|destinatario|facturar a"
    r"|facture a|rechnungsempfanger|faturar a)\s*:")
RE_NUMBER_LABEL = re.compile(
    r"^\s*[-·•]?\s*"
    r"(factura simplificada no|no de factura|factura no|ref factura"
    r"|invoice #|factura)")
RE_DATE_LABEL = re.compile(r"\bfecha[a-z\s]*:")
RE_IVA_LABEL = re.compile(r"\b(cuota iva|i\.v\.a\.?|iva)")
RE_RATE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
RE_CONCEPTO = re.compile(r"^\s*[-·•]?\s*concepto\b")
RE_SEPARATOR = re.compile(r"^\s*-{5,}\s*$")
RE_EMISOR = re.compile(r"^\s*[-·•]?\s*emisor\b")
RE_ITEM = re.compile(
    r"^\s*[-–—·]?\s*"
    r"(?P<desc>.*?)"
    r"(?:\s*\(\s*(?P<q1>\d+)\s*(?:uds?)?\.?\s*\)"
    r"|\s+[xX](?P<q2>\d+)\b"
    r"|\s+(?P<q3>\d+)(?=\s+[\d.,]+\s*€?\s*$))?"
    r"\s*(?:(?:\.{3,}|—|–|-|:)\s*)?"
    r"(?:EUR\s*)?"
    r"(?P<amount>-?\d[\d.,]*)"
    r"\s*€?\s*$",
    re.IGNORECASE)

# Most specific first: "total a pagar" must win over "total", and
# "base imponible" over "base".  "Subtotal" opens the line so it never
# activates "total".
BASE_LABELS = ("base imponible", "importe base", "subtotal", "base")
TOTAL_LABELS = ("total a pagar", "importe total", "total factura", "total")

_MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
     "agosto", "septiembre", "octubre", "noviembre", "diciembre"], 1)}
_MESES["setiembre"] = 9


def strip_invisible(text: str) -> tuple[str, int]:
    """Remove invisible characters; return (clean text, removed count).

    Some invoices interleave U+200B between digits so a naive parser reads
    '2' where a human reads '2.637,80'.  Category Cf (zero-width, bidi marks,
    soft hyphen) carries no text and is dropped; odd Zs separators become a
    normal space so they keep separating tokens.  Newlines are Cc and stay.
    """
    removed = 0
    out = []
    for char in text:
        cat = unicodedata.category(char)
        if cat == "Cf":
            removed += 1
        elif cat == "Zs" and char != " ":
            out.append(" ")
            removed += 1
        else:
            out.append(char)
    return ("".join(out), removed) if removed else (text, 0)


def native_reading(file_id: str, page_texts: list[str]) -> dict | None:
    """Build a schema-0.1 reading from embedded text, or None when any page
    lacks a real text layer (scans go to the vision stage)."""
    pages = []
    for number, raw in enumerate(page_texts, 1):
        text, _ = strip_invisible(raw)
        if len(text.strip()) < MIN_TEXT_CHARS:
            return None
        pages.append({
            "page": number,
            "blocks": [{
                "id": f"p{number}-b1",
                "kind": "other",
                "text": text,
                "rows": [],
                "uncertainties": [],
            }],
            "non_text_elements": [],
        })
    return {
        "schema_version": "0.1",
        "file_id": file_id,
        "capabilities": {"block_kinds": False, "tables": False, "layout": False},
        "pages": pages,
    }


def _flat(text: str) -> tuple[str, list[int]]:
    """Accent-free lowercase text plus a map flat index -> original index."""
    out: list[str] = []
    amap: list[int] = []
    for i, char in enumerate(text):
        norm = unicodedata.normalize("NFKD", char.lower())
        for c in norm:
            if unicodedata.combining(c):
                continue
            out.append(c)
            amap.append(i)
    return "".join(out), amap


def _ibans(line: "_Line") -> list[tuple[str, int, int]]:
    """Los IBAN de una linea, anclados en su etiqueta: (valor, inicio, fin)."""
    found = []
    for label in RE_IBAN_LABEL.finditer(line.flat):
        start = line.original(label.end())
        value = RE_IBAN.match(line.text[start:])
        if value:
            found.append((value.group().strip(), start + value.start(),
                          start + value.end()))
    return found


def _tax_ids(line: "_Line") -> list[tuple[str, int, int]]:
    """Los identificadores fiscales de una linea: (valor, inicio, fin)."""
    found = []
    for label in RE_TAX_LABEL.finditer(line.flat):
        start = line.original(label.end())
        value = RE_TAXID_VALUE.match(line.text[start:])
        if value:
            found.append((value.group(), start + value.start(), start + value.end()))
    return found


class _Line:
    __slots__ = ("amap", "block", "flat", "page", "start", "text")

    def __init__(self, page, block, start, text):
        self.page, self.block, self.start, self.text = page, block, start, text
        self.flat, self.amap = _flat(text)

    def link(self, rel_start: int, rel_end: int) -> dict:
        return {
            "page": self.page,
            "reference_ids": [self.block],
            "start": self.start + rel_start,
            "end": self.start + rel_end,
        }

    def original(self, flat_end: int) -> int:
        """Original-text offset just after flat prefix ``flat_end``."""
        return self.amap[flat_end - 1] + 1 if flat_end else 0


def _lines(reading: dict) -> list[_Line]:
    result = []
    for page in reading.get("pages", []):
        for block in page.get("blocks", []):
            offset = 0
            for raw in (block.get("text") or "").split("\n"):
                result.append(_Line(page["page"], block["id"], offset, raw))
                offset += len(raw) + 1
    return result


def _open_label(flat: str, labels: tuple[str, ...]) -> int | None:
    """Flat offset just after a line-opening label, else None.

    The label must open the line (optional dash/bullet/whitespace allowed) and
    must not be a prefix of a longer word: "subtotal" never activates "total".
    """
    pos = RE_LEAD.match(flat).end()
    for label in sorted(labels, key=len, reverse=True):
        if flat.startswith(label, pos):
            nxt = flat[pos + len(label):pos + len(label) + 1]
            if not nxt.isalpha():
                return pos + len(label)
    return None


def _find_label(flat: str, labels: tuple[str, ...]) -> tuple[int, int] | None:
    """Earliest (start, end) of a boundary-checked label anywhere in the line."""
    best: tuple[int, int] | None = None
    for label in labels:
        start = 0
        while True:
            i = flat.find(label, start)
            if i == -1:
                break
            prev_ok = i == 0 or not (
                flat[i - 1].isalnum() or flat[i - 1] in "._")
            nxt = flat[i + len(label):i + len(label) + 1]
            if prev_ok and not nxt.isalpha():
                if best is None or i < best[0]:
                    best = (i, i + len(label))
                break
            start = i + 1
    return best


def _amount(raw: str) -> str | None:
    """Canonical decimal string for '1.409,40', '1409.40' or '1.250'.

    The LAST separator is decimal only when 1 or 2 digits follow it; with 3 it
    was a thousands separator.  Mirrors normalize_decimal output shape.
    """
    s = (raw or "").strip().rstrip(".,")
    if not re.fullmatch(r"-?[\d.,]+", s) or not any(c.isdigit() for c in s):
        return None
    cut = max(s.rfind("."), s.rfind(","))
    if cut == -1:
        whole, frac = s, ""
    else:
        tail = s[cut + 1:]
        if tail.isdigit() and len(tail) in (1, 2):
            whole, frac = s[:cut], tail
        else:
            whole, frac = s, ""
    whole = whole.replace(".", "").replace(",", "")
    if not whole.lstrip("-").isdigit():
        return None
    try:
        value = Decimal(f"{whole}.{frac or '0'}")
    except InvalidOperation:
        return None
    out = format(value, "f")
    if "." in out:
        out = out.rstrip("0").rstrip(".")
    return out if out not in ("", "-0") else "0"


def _first_amount(segment: str) -> tuple[str, int, int] | None:
    """First amount in a fragment: skip rates (n%) and date-shaped tokens."""
    masked = RE_DATE.sub(lambda m: " " * len(m.group()), segment)
    masked = RE_DATE_ISO.sub(lambda m: " " * len(m.group()), masked)
    for match in RE_NUM.finditer(masked):
        if RE_PCT.match(masked, match.end()):
            continue
        value = _amount(match.group())
        if value is not None:
            return value, match.start(), match.end()
    return None


def _first_date(text: str) -> tuple[str, int, int] | None:
    """Earliest date in the text among DD/MM/YYYY, ISO and 'D de mes de YYYY'."""
    candidates: list[tuple[int, str, int, int]] = []
    for match in RE_DATE.finditer(text):
        day, month, year = map(int, match.groups())
        candidates.append((match.start(), "dmy", day, month, year, match.end()))
    for match in RE_DATE_ISO.finditer(text):
        year, month, day = map(int, match.groups())
        candidates.append((match.start(), "ymd", year, month, day, match.end()))
    for match in RE_DATE_LONG.finditer(text):
        day, mes, year = match.group(1), match.group(2), match.group(3)
        month = _MESES.get(_flat(mes)[0])
        if month:
            candidates.append(
                (match.start(), "long", int(day), month, int(year), match.end()))
    for entry in sorted(candidates):
        try:
            if entry[1] == "ymd":
                found = date(entry[2], entry[3], entry[4])
            else:
                found = date(entry[4], entry[3], entry[2])
        except ValueError:
            continue
        return found.isoformat(), entry[0], entry[5]
    return None


def _structural(line: _Line) -> bool:
    """A header/label line; item lines live strictly after the last one."""
    flat = line.flat
    return bool(
        RE_CUSTOMER.match(flat)
        or RE_CONCEPTO.match(flat)
        or RE_EMISOR.match(flat)
        or RE_NUMBER_LABEL.match(flat)
        or RE_DATE_LABEL.search(flat)
        or RE_PO.search(line.text)
        # A real identifier token, not the word "NIF"/"IBAN" in prose.
        or RE_NIF.search(line.text)
        or RE_IBAN.search(line.text)
    )


def _totals_boundary(line: _Line) -> bool:
    flat = line.flat
    return _open_label(flat, BASE_LABELS + TOTAL_LABELS) is not None or bool(
        _open_label(flat, ("cuota iva", "i.v.a.", "i.v.a", "iva")))


def _label_value(lines: list[_Line], labels: tuple[str, ...]):
    """First amount after a line-opening label; labels tried in order."""
    for label in labels:
        for line in lines:
            end = _open_label(line.flat, (label,))
            if end is None:
                continue
            segment = line.text[line.original(end):]
            found = _first_amount(segment)
            if found:
                value, s, e = found
                base = line.original(end)
                return value, line.link(base + s, base + e)
    return None, None


def extract(reading: dict) -> dict:
    """Extract the schema invoice plus evidence pointers from a reading."""
    file_id = reading["file_id"]
    lines = _lines(reading)
    invisible = sum(
        strip_invisible(block.get("text") or "")[1]
        for page in reading.get("pages", [])
        for block in page.get("blocks", []))
    gaps: list[str] = []
    pointers: dict[str, list[dict]] = {}

    def put(pointer: str, link: dict | None):
        if link is not None:
            pointers[pointer] = [link]

    # document_type -----------------------------------------------------
    document_type = "unknown"
    for line in lines:
        match = RE_DOCTYPE.search(line.text)
        if match:
            document_type = "invoice"
            put("/document_type", line.link(match.start(), match.end()))
            break
    if document_type == "unknown":
        gaps.append("document_type_unconfirmed")

    # invoice_number -----------------------------------------------------
    invoice_number = None
    for line in lines:
        match = RE_NUMBER_LABEL.match(line.flat)
        if not match:
            continue
        end = match.end(1)
        nxt = line.flat[end:end + 1]
        if nxt.isalpha():
            continue  # "facturar", never a label
        start = line.original(end)
        sep = re.match(r"\s*[:#]?\s*", line.text[start:])
        token = RE_TOKEN.match(line.text, start + sep.end())
        if not token:
            continue
        value = token.group()
        if (not any(c.isdigit() for c in value)
                or RE_DATE.fullmatch(value) or RE_DATE_ISO.fullmatch(value)
                or re.fullmatch(r"-?\d[\d.,]*", value)):
            continue
        invoice_number = value
        put("/invoice_number", line.link(token.start(), token.end()))
        break

    # issue_date ----------------------------------------------------------
    issue_date = None
    for line in lines:
        match = RE_DATE_LABEL.search(line.flat)
        if not match:
            continue
        start = line.original(match.end())
        found = _first_date(line.text[start:])
        if found:
            value, s, e = found
            issue_date = value
            put("/issue_date", line.link(start + s, start + e))
            break
    if issue_date is None:
        gaps.append("missing_issue_date")

    # purchase_order_reference ---------------------------------------------
    po_value = None
    seen_po: dict[str, tuple[_Line, re.Match]] = {}
    for line in lines:
        for match in RE_PO.finditer(line.text):
            seen_po.setdefault(match.group(), (line, match))
    if len(seen_po) == 1:
        line, match = next(iter(seen_po.values()))
        po_value = match.group()
        put("/purchase_order_reference", line.link(match.start(), match.end()))
    elif seen_po:
        gaps.append("ambiguous_purchase_order")

    # payment.iban -----------------------------------------------------------
    iban = None
    seen_iban: dict[str, tuple[_Line, re.Match]] = {}
    for line in lines:
        for value, start, end in _ibans(line):
            seen_iban.setdefault(
                re.sub(r"\s+", "", value).upper(), (line, start, end))
    if len(seen_iban) == 1:
        line, start, end = next(iter(seen_iban.values()))
        iban = next(iter(seen_iban))
        put("/payment/iban", line.link(start, end))
    elif seen_iban:
        gaps.append("ambiguous_iban")

    # customer + supplier tax ids ---------------------------------------------
    customer_name = customer_tax = None
    supplier_tax = None
    supplier_tax_ids: dict[str, tuple[_Line, re.Match]] = {}
    for line in lines:
        customer = RE_CUSTOMER.match(line.flat)
        if customer:
            start = line.original(customer.end())
            rest = re.match(r"\s*:?\s*", line.text[start:])
            name_start = start + rest.end()
            tail = line.text[name_start:]
            cut = len(tail)
            for sep in (" · ", " — ", " (", " CIF", "—"):
                i = tail.find(sep)
                if i != -1:
                    cut = min(cut, i)
            name = tail[:cut].strip()
            if name and customer_name is None:
                customer_name = name
                put("/customer/name",
                    line.link(name_start, name_start + len(name)))
            taxes = _tax_ids(line)
            if taxes and customer_tax is None:
                customer_tax, start, end = taxes[0]
                put("/customer/tax_id", line.link(start, end))
            continue
        if "cliente" in line.flat:
            continue  # the payer's CIF line is never the supplier's
        for value, start, end in _tax_ids(line):
            supplier_tax_ids.setdefault(value, (line, start, end))
    supplier_tax_ids.pop(customer_tax, None)
    if len(supplier_tax_ids) == 1:
        line, start, end = next(iter(supplier_tax_ids.values()))
        supplier_tax = next(iter(supplier_tax_ids))
        put("/supplier/tax_id", line.link(start, end))
    elif supplier_tax_ids:
        gaps.append("ambiguous_supplier_tax_id")

    # supplier.name -------------------------------------------------------------
    supplier_name = None
    for line in lines:
        match = RE_EMISOR.match(line.flat)
        if not match:
            continue
        start = line.original(match.end())
        rest = re.match(r"\s*:?\s*", line.text[start:])
        name_start = start + rest.end()
        tail = line.text[name_start:]
        cut = len(tail)
        for sep in (" · ", " NIF", " CIF", " (", "—"):
            i = tail.find(sep)
            if i != -1:
                cut = min(cut, i)
        name = tail[:cut].strip()
        if name:
            supplier_name = name
            put("/supplier/name", line.link(name_start, name_start + len(name)))
        break
    if supplier_name is None:
        for line in lines:
            text = line.text.strip()
            if (not text or ":" in text or re.search(r"\d", text)
                    or line.flat.strip() in ("factura", "invoice")
                    or line.flat.strip().startswith("factura simplificada")):
                continue
            name_start = line.text.index(text[0])
            supplier_name = text
            put("/supplier/name",
                line.link(name_start, name_start + len(text)))
            break
    if supplier_name is not None and not RE_LEGAL.search(supplier_name):
        supplier_name = None
        pointers.pop("/supplier/name", None)
        gaps.append("supplier_name_unconfirmed")

    # lines ----------------------------------------------------------------------
    start_idx = 0
    for i, line in enumerate(lines):
        if _structural(line):
            start_idx = i + 1
    end_idx = len(lines)
    for i in range(start_idx, len(lines)):
        if _totals_boundary(lines[i]):
            end_idx = i
            break
    rows = []
    for line in lines[start_idx:end_idx]:
        text = line.text
        if not text.strip() or RE_SEPARATOR.match(text) or RE_CONCEPTO.match(line.flat):
            continue
        match = RE_ITEM.match(text)
        if not match:
            continue
        desc = match.group("desc").strip().rstrip(".—–-:· ")
        amount = _amount(match.group("amount"))
        if not desc or not re.search(r"[a-zñáéíóú]", desc, re.IGNORECASE) \
                or amount is None:
            continue
        desc_start = match.start("desc") + (len(match.group("desc")) - len(match.group("desc").lstrip()))
        qty = match.group("q1") or match.group("q2") or match.group("q3")
        position = len(rows) + 1
        row = {"position": position, "description": desc,
               "quantity": qty, "amount": amount}
        rows.append(row)
        put(f"/lines/{position - 1}/description",
            line.link(desc_start, desc_start + len(desc)))
        if qty is not None:
            group = "q1" if match.group("q1") else "q2" if match.group("q2") else "q3"
            put(f"/lines/{position - 1}/quantity",
                line.link(match.start(group), match.end(group)))
        put(f"/lines/{position - 1}/amount",
            line.link(match.start("amount"), match.end("amount")))

    # totals ----------------------------------------------------------------------
    taxable_base, link = _label_value(lines, BASE_LABELS)
    put("/totals/taxable_base", link)
    total, link = _label_value(lines, TOTAL_LABELS)
    put("/totals/total", link)

    # taxes -------------------------------------------------------------------------
    taxes = []
    for line in lines:
        found = _find_label(line.flat, ("cuota iva", "i.v.a.", "i.v.a", "iva"))
        if not found:
            continue
        label_s, label_e = found
        seg_start = line.original(label_e)
        segment = line.text[seg_start:]
        amount_found = _first_amount(segment)
        if not amount_found:
            continue
        amount, a_s, a_e = amount_found
        rate = None
        rate_match = RE_RATE.search(segment)
        if rate_match:
            rate = rate_match.group(1).replace(",", ".")
            rate_link = line.link(
                seg_start + rate_match.start(1), seg_start + rate_match.end(1))
        row = {"label": "IVA", "rate_percent": rate, "amount": amount}
        taxes.append(row)
        orig_label_s = line.original(label_s + 1) - 1 if label_s else 0
        orig_label_e = line.original(label_e)
        put("/taxes/0/label", line.link(orig_label_s, orig_label_e))
        if rate_match:
            put("/taxes/0/rate_percent", rate_link)
        put("/taxes/0/amount", line.link(seg_start + a_s, seg_start + a_e))
        break
    if not taxes:
        gaps.append("missing_vat_row")

    # currency ----------------------------------------------------------------------
    # Codes are word-bounded; symbols match anywhere. More than one distinct
    # currency on the document is ambiguous rather than a guess.
    currency = None
    hits = []
    for line in lines:
        for match in RE_CURRENCY.finditer(line.text):
            hits.append((CURRENCY_CODES[match.group()], line, match))
    distinct = {code for code, _line, _match in hits}
    if len(distinct) == 1:
        currency = hits[0][0]
        put("/currency", hits[0][1].link(hits[0][2].start(), hits[0][2].end()))
    elif len(distinct) > 1:
        gaps.append("ambiguous_currency")

    invoice = {
        "schema_version": "0.1",
        "file_id": file_id,
        "document_type": document_type,
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "purchase_order_reference": po_value,
        "currency": currency,
        "supplier": {"name": supplier_name, "tax_id": supplier_tax,
                     "location": None, "address": None},
        "customer": {"name": customer_name, "tax_id": customer_tax,
                     "location": None, "address": None},
        "payment": {"iban": iban},
        "lines": rows,
        "taxes": taxes,
        "totals": {"taxable_base": taxable_base, "total": total},
        "annotations": [],
        "additional_fields": [],
        "issues": [],
    }
    return {"invoice": invoice, "evidence": {"pointers": pointers},
            "gaps": gaps, "invisible_chars": invisible}


def _at(invoice: dict, pointer: str):
    node = invoice
    for part in pointer[1:].split("/"):
        try:
            if isinstance(node, list):
                node = node[int(part)]
            elif isinstance(node, dict):
                node = node.get(part)
            else:
                return None
        except (IndexError, ValueError):
            return None
        if node is None:
            return None
    return node


def accept(invoice: dict, checks: dict) -> tuple[bool, list[str]]:
    """The cascade gate: every required pointer present, lines read, and the
    interpretation checks green.  An arithmetic mismatch is a finding for the
    evaluator, not a reason to pay for vision."""
    reasons: list[str] = []
    for pointer in GATE_POINTERS:
        if _at(invoice, pointer) is None:
            reasons.append(f"missing:{pointer}")
    if not invoice.get("lines"):
        reasons.append("missing:/lines")
    if checks.get("status") != "completed":
        failed = [key for key, ok in (checks.get("checks") or {}).items() if not ok]
        reasons.extend(f"check:{key}" for key in failed or ["status"])
    return not reasons, reasons
