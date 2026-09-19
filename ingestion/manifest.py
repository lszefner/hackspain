from __future__ import annotations

import hashlib
import json
from pathlib import Path


def discover(
    input_dir: str | None = None, manifest_path: str | None = None
) -> list[dict]:
    if bool(input_dir) == bool(manifest_path):
        raise ValueError("Specify exactly one of --input or --manifest")
    if input_dir:
        root = Path(input_dir).resolve()
        entries = [
            {"path": str(p.relative_to(root)), "file_id": p.name}
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix.lower() == ".pdf"
        ]
    else:
        source = Path(manifest_path).resolve()
        root = source.parent
        value = json.loads(source.read_text())
        entries = value["documents"] if isinstance(value, dict) else value
    if not entries:
        raise ValueError("No input documents")
    seen, output = set(), []
    for index, entry in enumerate(entries):
        path = (root / entry["path"]).resolve()
        file_id = entry.get("file_id", path.name)
        if file_id in seen:
            raise ValueError("Duplicate file_id in manifest")
        if file_id != path.name:
            raise ValueError("file_id must be exact source filename")
        seen.add(file_id)
        # Missing inputs remain in the denominator with an explicit registration error.
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            error = (
                None
                if not entry.get("sha256") or entry["sha256"] == digest
                else "source_hash_mismatch"
            )
        except OSError:
            digest, error = None, "source_unreadable"
        output.append(
            {
                "file_id": file_id,
                "relative_path": entry["path"],
                "local_path": str(path),
                "source_sha256": digest,
                "ordinal": index,
                "error": error,
            }
        )
    return output
