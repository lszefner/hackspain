"""One-off export of the current local revision data to a static JSON file
bundled into the Next.js app, so the Vercel deployment can show real data
without a live backend. Re-run and re-commit whenever local/data changes
should be reflected on the deployed site; not wired into any automatic
build step on purpose.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.server import _facturas, _resumen  # noqa: E402
from backend.server import STORE  # noqa: E402

OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "data" / "snapshot.json"


def _factura_detalle(file_id: str) -> dict:
    row = STORE.get(file_id)
    if row is None:
        return {"file_id": file_id, "estado": "pendiente", "decision": None,
                "checks": [], "campos": {}, "error": None}
    return {
        "file_id": file_id,
        "estado": row["estado"],
        "decision": row.get("decision"),
        "checks": json.loads(row["checks"]) if row["checks"] else [],
        "campos": json.loads(row["raw_invoice"]) if row["raw_invoice"] else {},
        "error": row.get("error"),
    }


def build() -> dict:
    resumen = _resumen()
    resumen["error"] = (
        "Datos de ejemplo (instantanea local, sin backend en vivo) "
        "mientras el equipo termina el backend desplegado."
    )
    resumen["procesando"] = False
    facturas = {file_id: _factura_detalle(file_id) for file_id in _facturas()}
    return {"resumen": resumen, "facturas": facturas}


def main() -> int:
    snapshot = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
