"""Freeze the mock desk into files the Next app can serve on Vercel.

`serve.py` folds every answer out of `state.py`, which parses the 500 PDFs and
the workbook on first call. None of that survives in a serverless function, so
this writes the same folds to disk once and the routes under
frontend/src/app/api/ read them back. Nothing here is invented: every number
comes from the same functions the local Python server calls.

Two destinations, on purpose:
  data/    JSON the route handlers `import`, so Vercel bundles it into the
           function instead of hoping the public dir is on its filesystem.
  public/  the page and the documents, which the CDN serves directly.

    .venv/bin/python desk/mock/export_static.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("HELMCODE_API_KEY", "export-only")   # serve.py needs one to import

import serve                                               # noqa: E402
import state                                               # noqa: E402

DATA = ROOT / "frontend" / "src" / "desk-data"
PUBLIC = ROOT / "frontend" / "public" / "desk"


def write(name: str, obj) -> None:
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    (DATA / name).write_text(body, encoding="utf-8")
    print(f"  data/{name:24} {len(body) / 1024:8.1f} KB")


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    PUBLIC.mkdir(parents=True, exist_ok=True)

    rows = state.archive()
    write("archive.json", rows)
    write("blocks.json", {n: state.block(n) for n, _ in serve.INTENTS})
    write("facts.json", state.facts())
    write("summary.json", state.summary())
    write("rules.json", state.rules_view())
    write("intents.json", [[n, list(k)] for n, k in serve.INTENTS])
    write("dossiers.json", {r["file"]: d for r in rows
                            if (d := state.dossier(r["file"]))})
    # serve.py answers a question that names a file with this block instead
    write("invoice_blocks.json", {r["file"]: state.invoice_block(r["file"])
                                  for r in rows})
    write("system.json", {"system": serve.SYSTEM, "model": serve.MODEL,
                          "base_url": serve.BASE_URL})

    (PUBLIC / "sepa.xml").write_text(state.sepa_xml(), encoding="utf-8")

    # the page itself, byte for byte: one UI, two ways of serving it
    shutil.copy2(HERE / "index.html", PUBLIC / "index.html")
    print(f"  public/index.html        {(PUBLIC / 'index.html').stat().st_size / 1024:8.1f} KB")

    pdfs = PUBLIC / "facturas"
    pdfs.mkdir(parents=True, exist_ok=True)
    for src in sorted(state.CAJA.glob("*.pdf")):
        shutil.copy2(src, pdfs / src.name)
    size = sum(f.stat().st_size for f in pdfs.glob("*.pdf")) / 1024 / 1024
    print(f"  public/facturas/*.pdf    {size:8.1f} MB  ({len(list(pdfs.glob('*.pdf')))} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
