"""Where the engine finds La Caja. Self-contained on purpose.

The invoice PDFs and the master workbook live in `caja/` (the live copy) or,
for a fresh clone, in the newest `caja_de_alberto/vN/` snapshot. Resolving
that here — rather than importing the legacy `alberto` package — keeps the
engine independent of the track it grew out of.

Nothing here is required when the caller supplies its own inputs: the web
path passes `--input-dir` / `REVISION_INPUT_DIR`, and an explicit
`--sources` mapping pins the workbook. These are only the defaults.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "caja_de_alberto"
LIVE = ROOT / "caja"

_VERSION = re.compile(r"^v(\d+)$")


def _relative_if_inside(path: Path) -> Path:
    """Relative to the working directory when it lives under it, else absolute.

    This path is recorded in run inputs, so an absolute '/Users/...' would tie
    a durable record to one machine.
    """
    try:
        return path.relative_to(Path.cwd())
    except ValueError:
        return path


def snapshots() -> list[Path]:
    """Committed captures, oldest first, ordered by number so v10 follows v9."""
    if not SNAPSHOTS.is_dir():
        return []
    found = []
    for child in SNAPSHOTS.iterdir():
        match = _VERSION.match(child.name)
        if match and (child / "facturas").is_dir():
            found.append((int(match.group(1)), child))
    return [path for _, path in sorted(found)]


def resolver() -> Path:
    """`ALBERTO_CAJA` if set, else the live copy, else the newest snapshot."""
    override = os.environ.get("ALBERTO_CAJA")
    if override:
        return Path(override)
    if (LIVE / "facturas").is_dir():
        return _relative_if_inside(LIVE)
    found = snapshots()
    return _relative_if_inside(found[-1] if found else LIVE)


def facturas() -> Path:
    """The resolved folder of invoice PDFs. May not exist; callers decide."""
    return resolver() / "facturas"


def excel() -> Path | None:
    """The master workbook inside the resolved Caja, if there is one."""
    return next(iter(sorted(resolver().glob("*.xlsx"))), None)
