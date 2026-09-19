from __future__ import annotations

import posixpath
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from rules_ingestion import normalize as N

ROOT = Path(__file__).resolve().parent.parent
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
COLUMNS = {
    "Proveedores": {"ID": "id", "Razon Social": "razon_social", "NIF": "nif",
                    "IBAN": "iban", "Ciudad": "ciudad", "Condiciones": "condiciones"},
    "Pedidos_2026": {"Pedido": "pedido", "ProveedorID": "proveedor_id", "NIF": "nif",
                     "Importe_Total": "importe_total", "Estado": "estado_excel",
                     "Fecha_Pedido": "fecha_pedido"},
}


def load(path: Path = ROOT / "FINAL_v7_DEFINITIVO_ahorasi.xlsx") -> tuple[dict, dict]:
    with zipfile.ZipFile(path) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            shared = ["".join(n.itertext()) for n in ET.fromstring(book.read("xl/sharedStrings.xml"))]
        links = {n.attrib["Id"]: n.attrib["Target"]
                 for n in ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))}
        sheets = {n.attrib["name"]: links[n.attrib[REL]]
                  for n in ET.fromstring(book.read("xl/workbook.xml")).findall("m:sheets/m:sheet", NS)}
        result = []
        for name, mapping in COLUMNS.items():
            target = sheets[name]
            target = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            rows = ET.fromstring(book.read(target)).findall("m:sheetData/m:row", NS)
            headers, table = {}, {}
            for index, row in enumerate(rows):
                cells = {}
                for cell in row.findall("m:c", NS):
                    col = "".join(c for c in cell.attrib["r"] if c.isalpha())
                    value = cell.findtext("m:v", default="", namespaces=NS)
                    if cell.attrib.get("t") == "s":
                        value = shared[int(value)]
                    elif cell.attrib.get("t") == "inlineStr":
                        value = "".join(n.text or "" for n in cell.findall(".//m:t", NS))
                    cells[col] = (float(value) if cell.attrib.get("t", "n") == "n" and value
                                  else N.norm_text(value))
                if index == 0:
                    headers = cells
                    missing = set(mapping) - set(headers.values())
                    if missing:
                        raise ValueError("Faltan columnas del maestro: " + ", ".join(sorted(missing)))
                    continue
                record = {mapping[label]: cells.get(col) for col, label in headers.items() if label in mapping}
                key = record.get("id" if name == "Proveedores" else "pedido")
                if not key:
                    continue
                for field, normalize in (("nif", N.norm_nif), ("iban", N.norm_iban),
                                         ("importe_total", N.norm_importe), ("fecha_pedido", N.norm_fecha)):
                    if field in record:
                        record[field] = normalize(record[field])
                table[key] = record
            result.append(table)
    return result[0], result[1]
