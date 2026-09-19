"""Drop-in local replacements for ingestion.storage's Postgres/Supabase backends.

ingestion.pipeline.Pipeline talks to `repo` and `storage` through plain method
calls (no isinstance checks), so a same-shaped local object works unmodified.
This machine's network only allows outbound HTTPS (port 5432/6543 to Supabase
are firewalled), so real Postgres durability is not reachable here. This
module keeps job/attempt/artifact bookkeeping in memory for one run (no
crash-recovery across process restarts) and stores artifact bytes as local
content-addressed files instead of Supabase Storage. Nothing here changes
ingestion/*.py.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> float:
    return time.time()


class LocalStorage:
    """Content-addressed local filesystem stand-in for SupabaseStorage."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, kind: str, content_type: str = "application/octet-stream"):
        from ingestion.storage import StorageRef, sha256_bytes

        digest = sha256_bytes(data)
        safe_kind = kind.strip().replace("/", "-")
        object_key = f"sha256/{digest[:2]}/{digest}/{safe_kind}"
        path = self.root / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return StorageRef(digest, object_key, len(data), content_type, safe_kind)

    def get(self, object_key: str) -> bytes:
        return (self.root / object_key).read_bytes()

    def preflight(self) -> dict:
        return {"public": False, "local": True}


class LocalRepository:
    """In-memory stand-in for PostgresRepository: same call shapes, no SQL.

    Not multi-process safe and not crash-durable -- fine for a local,
    single-run demo interface. Real durability lives in ingestion/storage.py
    against Supabase, unmodified.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.batches: dict[str, dict] = {}
        self.inputs: dict[str, dict] = {}
        self.inputs_by_key: dict[tuple, str] = {}  # (batch_id, relative_path) -> id
        self.jobs: dict[str, dict] = {}
        self.jobs_by_work_key: dict[str, str] = {}
        self.attempts: dict[str, dict] = {}
        self.artifacts: dict[str, dict] = {}
        self.artifacts_by_key: dict[tuple, str] = {}  # (sha256, kind) -> id
        self._result_rows: dict[tuple, dict] = {}  # (input_id, interpreter) -> row

    # ---- batches ----
    def create_batch(self, manifest, config=None, *, status="pending"):
        with self._lock:
            row = {
                "id": _uuid(),
                "manifest": manifest,
                "config": dict(config or {}),
                "status": status,
                "created_at": _now(),
            }
            self.batches[row["id"]] = row
            return dict(row)

    def set_batch_status(self, batch_id, status):
        with self._lock:
            row = self.batches[str(batch_id)]
            row["status"] = status
            return dict(row)

    def get_batch(self, batch_id):
        return dict(self.batches[str(batch_id)]) if str(batch_id) in self.batches else None

    # ---- inputs ----
    def register_input(self, batch_id, relative_path, *, file_name=None,
                       content_hash=None, object_key=None, size_bytes=None):
        with self._lock:
            key = (str(batch_id), relative_path)
            existing_id = self.inputs_by_key.get(key)
            if existing_id:
                row = self.inputs[existing_id]
                if file_name and not row.get("file_name"):
                    row["file_name"] = file_name
                if content_hash and not row.get("content_hash"):
                    row["content_hash"] = content_hash
                if object_key and not row.get("object_key"):
                    row["object_key"] = object_key
                if size_bytes and not row.get("size_bytes"):
                    row["size_bytes"] = size_bytes
                return dict(row)
            row = {
                "id": _uuid(),
                "batch_id": str(batch_id),
                "relative_path": relative_path,
                "file_name": file_name or relative_path.rsplit("/", 1)[-1],
                "content_hash": content_hash,
                "object_key": object_key,
                "size_bytes": size_bytes,
            }
            self.inputs[row["id"]] = row
            self.inputs_by_key[key] = row["id"]
            return dict(row)

    def get_input(self, input_id):
        row = self.inputs.get(str(input_id))
        return dict(row) if row else None

    # ---- jobs ----
    def ensure_job(self, *, batch_id, input_id, stage, input_artifact_hash=None,
                   reading_hash=None, provider, model, config_version=None,
                   provider_revision=None, prompt_version=None, adapter_version=None,
                   schema_hash=None, settings=None, work_key=None, max_attempts=3):
        with self._lock:
            if work_key is None:
                from ingestion.storage import canonical_json_bytes, sha256_bytes

                key_value = {
                    "input": input_artifact_hash, "reading": reading_hash, "stage": stage,
                    "provider": provider, "model": model,
                    "provider_revision": provider_revision or "unknown",
                    "config": config_version, "prompt": prompt_version,
                    "adapter": adapter_version, "schema": schema_hash,
                    "settings": dict(settings or {}),
                }
                work_key = sha256_bytes(canonical_json_bytes(key_value))
            existing_id = self.jobs_by_work_key.get(work_key)
            if existing_id:
                return dict(self.jobs[existing_id])
            row = {
                "id": _uuid(), "batch_id": str(batch_id), "input_id": str(input_id),
                "stage": stage, "provider": provider, "model": model,
                "work_key": work_key, "max_attempts": max_attempts,
                "state": "pending", "attempt_count": 0,
                "lease_owner": None, "lease_token": None, "lease_expires_at": 0,
                "next_attempt_at": 0, "artifact_id": None, "last_error": None,
            }
            self.jobs[row["id"]] = row
            self.jobs_by_work_key[work_key] = row["id"]
            return dict(row)

    def get_job(self, job_id):
        row = self.jobs.get(str(job_id))
        return dict(row) if row else None

    def list_jobs(self, batch_id):
        with self._lock:
            rows = self.jobs.values()
            if batch_id is not None:
                rows = [r for r in rows if r["batch_id"] == str(batch_id)]
            return [dict(r) for r in rows]

    def claim(self, *, worker_id=None, lease_seconds=300, job_id=None, stage=None):
        with self._lock:
            now = _now()
            candidates = [
                r for r in self.jobs.values()
                if r["state"] in ("pending", "retry_wait")
                and r["next_attempt_at"] <= now
                and r["attempt_count"] < r["max_attempts"]
                and (job_id is None or r["id"] == str(job_id))
                and (stage is None or r["stage"] == stage)
            ]
            if not candidates:
                return None
            row = candidates[0]
            row["state"] = "running"
            row["lease_owner"] = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
            row["lease_token"] = _uuid()
            row["lease_expires_at"] = now + lease_seconds
            return dict(row)

    def claim_recovery(self, job_id, *, worker_id=None, lease_seconds=300):
        with self._lock:
            row = self.jobs.get(str(job_id))
            if row is None or row["state"] != "unknown":
                return None
            row["state"] = "running"
            row["lease_owner"] = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
            row["lease_token"] = _uuid()
            row["lease_expires_at"] = _now() + lease_seconds
            return dict(row)

    def begin_attempt(self, job_id, claim_token, *, request_id=None):
        with self._lock:
            job = self.jobs.get(str(job_id))
            if (job is None or job["state"] != "running"
                    or job["lease_token"] != str(claim_token)
                    or job["lease_expires_at"] <= _now()
                    or job["attempt_count"] >= job["max_attempts"]):
                raise PermissionError("job is not owned by this lease")
            job["attempt_count"] += 1
            row = {
                "id": _uuid(), "job_id": job["id"],
                "attempt_number": job["attempt_count"],
                "provider_request_id": request_id, "status": "running",
                "usage": {}, "raw_artifact_ids": [], "latency_seconds": None,
                "request_metadata": {},
            }
            self.attempts[row["id"]] = row
            return dict(row)

    def list_attempts(self, job_id):
        with self._lock:
            rows = [r for r in self.attempts.values() if r["job_id"] == str(job_id)]
            rows.sort(key=lambda r: r["attempt_number"])
            return [dict(r) for r in rows]

    def record_request_id(self, attempt_id, request_id):
        with self._lock:
            row = self.attempts[str(attempt_id)]
            row["provider_request_id"] = request_id
            return dict(row)

    def record_attempt_artifact(self, attempt_id, artifact_id):
        with self._lock:
            row = self.attempts[str(attempt_id)]
            row["raw_artifact_ids"] = [*row["raw_artifact_ids"], str(artifact_id)]
            return dict(row)

    def finish_attempt_metadata(self, attempt_id, *, usage=None, raw_artifact_ids=(),
                                latency_seconds=None, request_metadata=None):
        with self._lock:
            row = self.attempts[str(attempt_id)]
            if usage is not None:
                row["usage"] = usage
            row["raw_artifact_ids"] = [*row["raw_artifact_ids"],
                                      *(str(v) for v in raw_artifact_ids)]
            if latency_seconds is not None:
                row["latency_seconds"] = latency_seconds
            if request_metadata is not None:
                row["request_metadata"] = request_metadata
            return dict(row)

    # ---- artifacts ----
    def save_artifact(self, ref, *, payload=None, parent_artifact_ids=()):
        data = asdict(ref) if not isinstance(ref, Mapping) else dict(ref)
        digest, object_key = data.get("sha256"), data.get("object_key")
        key = (digest, data.get("kind", "artifact"))
        with self._lock:
            existing_id = self.artifacts_by_key.get(key)
            if existing_id:
                return dict(self.artifacts[existing_id])
            row = {
                "id": _uuid(), "sha256": digest, "kind": data.get("kind", "artifact"),
                "object_key": object_key,
                "content_type": data.get("content_type", "application/octet-stream"),
                "byte_size": int(data.get("byte_size", 0)),
                "payload": payload,
                "parent_artifact_ids": [str(v) for v in parent_artifact_ids],
                "verified_at": _now(),
            }
            self.artifacts[row["id"]] = row
            self.artifacts_by_key[key] = row["id"]
            return dict(row)

    def get_artifact(self, artifact_id):
        row = self.artifacts.get(str(artifact_id))
        return dict(row) if row else None

    # ---- completion ----
    def complete(self, job_id, claim_token, artifact_id, *, status="succeeded",
                result_status=None, interpreter=None, error=None):
        with self._lock:
            job = self.jobs.get(str(job_id))
            if (job is None or job["lease_token"] != str(claim_token)
                    or job["state"] != "running"):
                raise PermissionError("job is not owned by this lease")
            artifact = self.artifacts.get(str(artifact_id))
            if artifact is None or artifact.get("verified_at") is None:
                raise ValueError("artifact does not exist or is not verified")
            job.update(state=status, artifact_id=str(artifact_id), last_error=None,
                      lease_owner=None, lease_token=None, lease_expires_at=0)
            for attempt in self.attempts.values():
                if attempt["job_id"] == job["id"] and attempt["status"] == "running":
                    attempt["status"] = status
                    attempt["error"] = dict(error) if error else None
            if interpreter:
                persisted = result_status or ("completed" if status == "succeeded" else status)
                self._result_rows[(job["input_id"], interpreter)] = {
                    "input_id": job["input_id"], "interpreter": interpreter,
                    "job_id": job["id"], "artifact_id": str(artifact_id),
                    "status": persisted, "error": dict(error) if error else None,
                }
            return dict(job)

    def fail(self, job_id, claim_token, error, *, retryable=True, unknown=False,
             retry_after_seconds=0):
        state = "unknown" if unknown else ("retry_wait" if retryable else "failed")
        error_payload = error if isinstance(error, Mapping) else {"message": error}
        with self._lock:
            job = self.jobs.get(str(job_id))
            if job is None or job["state"] != "running" or job["lease_token"] != str(claim_token):
                raise PermissionError("job is not owned by this lease")
            if state == "retry_wait" and job["attempt_count"] >= job["max_attempts"]:
                state = "failed"
            job.update(state=state, last_error=dict(error_payload),
                      next_attempt_at=_now() + max(0, retry_after_seconds),
                      lease_owner=None, lease_token=None, lease_expires_at=0)
            for attempt in self.attempts.values():
                if attempt["job_id"] == job["id"] and attempt["status"] == "running":
                    attempt["status"] = "unknown" if unknown else "failed"
                    attempt["error"] = dict(error_payload)
            return dict(job)

    # ---- results ----
    def set_result(self, input_id, interpreter, artifact_id, status, *, error=None, job_id=None):
        with self._lock:
            if job_id is None:
                candidates = [j for j in self.jobs.values() if j["input_id"] == str(input_id)]
                if not candidates:
                    raise ValueError("input has no job to attach result")
                job_id = sorted(candidates, key=lambda j: j["id"])[-1]["id"]
            row = {
                "input_id": str(input_id), "interpreter": interpreter,
                "job_id": str(job_id),
                "artifact_id": str(artifact_id) if artifact_id else None,
                "status": status, "error": dict(error) if error else None,
            }
            self._result_rows[(str(input_id), interpreter)] = row
            return dict(row)

    def results(self, batch_id):
        with self._lock:
            out = []
            for (input_id, interpreter), row in self._result_rows.items():
                inp = self.inputs.get(input_id)
                if inp and inp["batch_id"] == str(batch_id):
                    out.append({**row, "relative_path": inp["relative_path"],
                              "file_name": inp["file_name"]})
            return out

    def close(self):
        pass
