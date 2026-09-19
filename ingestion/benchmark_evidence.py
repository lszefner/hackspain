"""Export the persisted OCR submission requests for offline benchmark import."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

from .contracts import canonical_bytes, digest
from .export import write_json


def export_ocr_requests(batch_id, output):
    root = Path(output)
    source = json.loads((root / "manifest.json").read_text())["documents"]
    expected = {entry["file_id"]: entry.get("source_sha256") for entry in source}
    grouped = {fid: [] for fid in expected}
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        rows = conn.execute(
            """
            SELECT DISTINCT i.file_name, i.content_hash, a.sha256, a.payload
            FROM ingestion.inputs i
            JOIN ingestion.jobs j ON j.input_id = i.id
            JOIN ingestion.attempts t ON t.job_id = j.id
            CROSS JOIN LATERAL jsonb_array_elements_text(t.raw_artifact_ids) link(artifact_id)
            JOIN ingestion.artifacts a ON a.id = link.artifact_id::uuid
            WHERE i.batch_id = %s AND j.stage = 'reading'
              AND a.kind = 'provider-request' AND a.payload->>'method' = 'POST'
            ORDER BY i.file_name, a.sha256
        """,
            (batch_id,),
        ).fetchall()
    for row in rows:
        if (
            row["file_name"] not in expected
            or row["content_hash"] != expected[row["file_name"]]
        ):
            raise ValueError("Batch and export source identity mismatch")
        if digest(canonical_bytes(row["payload"])) != row["sha256"]:
            raise ValueError("Persisted request payload hash mismatch")
        grouped[row["file_name"]].append(row["payload"])
    for fid, requests in grouped.items():
        if requests:
            stem = hashlib.sha256(fid.encode()).hexdigest()
            write_json(root / "benchmark-import" / stem / "ocr-request.json", requests)
    return {
        "batch_id": batch_id,
        "documents_with_ocr_requests": sum(bool(v) for v in grouped.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--export", required=True)
    args = parser.parse_args()
    load_dotenv(override=False)
    print(json.dumps(export_ocr_requests(args.batch, args.export)))


if __name__ == "__main__":
    main()
