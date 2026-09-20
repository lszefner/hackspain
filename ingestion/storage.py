"""Durable persistence and immutable artifact storage for invoice ingestion.

The worker is intentionally the only consumer of this module.  Postgres stores
small, searchable metadata and canonical JSON; Supabase Storage stores the
original bytes and provider responses.  A storage upload is verified before
its metadata is published to Postgres.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import secrets
import uuid
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any
from urllib.parse import quote

from .artifact_cache import cached, remember

try:  # psycopg is an installation dependency, but keep imports lazy for docs/tests.
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - exercised only in minimal environments
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:  # pragma: no cover - exercised only in minimal environments
    httpx = None  # type: ignore[assignment]


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize JSON deterministically for hashes and immutable artifacts."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _uuid(value: str | uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True)
class StorageRef:
    sha256: str
    object_key: str
    byte_size: int
    content_type: str
    kind: str


class SupabaseStorage:
    """Upload immutable objects to a private Supabase Storage bucket.

    Objects are content addressed.  Existing objects are fetched and verified
    rather than replaced, so a retry can safely reuse a successful upload.
    ``SUPABASE_SECRET_KEY`` backend credentials must be supplied by the worker environment;
    this class never persists or logs them.
    """

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        bucket: str = "invoice-ingestion-private",
        client: Any | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SECRET_KEY", "")
        self.bucket = bucket
        self._verified = OrderedDict()
        self._verified_lock = Lock()
        if client is not None:
            self.client = client
        else:
            if httpx is None:
                raise RuntimeError("httpx is required for SupabaseStorage")
            if not self.url or not self.key:
                raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
            self.client = httpx.Client(timeout=timeout)

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        # Modern secret keys are opaque API keys, not bearer JWTs.
        headers = {"apikey": self.key}
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def _url(self, object_key: str) -> str:
        return f"{self.url}/storage/v1/object/{quote(self.bucket, safe='')}/{quote(object_key, safe='/')}"

    def put(
        self, data: bytes, kind: str, content_type: str = "application/octet-stream"
    ) -> StorageRef:
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        digest = sha256_bytes(data)
        safe_kind = kind.strip().replace("/", "-")
        if not safe_kind:
            raise ValueError("artifact kind is required")
        object_key = f"sha256/{digest[:2]}/{digest}/{safe_kind}"
        with self._verified_lock:
            if object_key in self._verified:
                self._verified.move_to_end(object_key)
                return self._verified[object_key]
        url = self._url(object_key)

        # POST with x-upsert=false is immutable.  A conflict is expected for a
        # concurrent/retried upload; either path is followed by byte verification.
        response = self.client.post(
            url,
            content=data,
            headers={**self._headers(content_type), "x-upsert": "false"},
        )
        status = getattr(response, "status_code", 200)
        if status >= 400 and status not in (409, 400):
            response.raise_for_status()

        downloaded = self.client.get(url, headers=self._headers())
        downloaded.raise_for_status()
        actual = downloaded.content
        actual_hash = sha256_bytes(actual)
        if actual_hash != digest or len(actual) != len(data):
            raise OSError(f"immutable artifact verification failed for {object_key}")
        remember(self, object_key, actual)
        ref = StorageRef(digest, object_key, len(data), content_type, safe_kind)
        with self._verified_lock:
            self._verified[object_key] = ref
            if len(self._verified) > 256:
                self._verified.popitem(last=False)
        return ref

    def preflight(self) -> dict[str, Any]:
        """Verify the configured bucket exists and is private."""
        response = self.client.get(
            f"{self.url}/storage/v1/bucket/{quote(self.bucket, safe='')}",
            headers=self._headers(),
        )
        if response.status_code >= 400:
            try:
                error = response.json()
            except ValueError:
                error = {}
            if response.status_code == 404 or str(error.get("statusCode")) == "404":
                raise ValueError(
                    f"Storage bucket {self.bucket!r} does not exist. "
                    "Set SUPABASE_STORAGE_BUCKET to the private bucket created by the migration "
                    "(invoice-ingestion-private)."
                )
            raise ValueError(
                f"Storage bucket preflight HTTP {response.status_code}; "
                "check SUPABASE_URL and SUPABASE_SECRET_KEY."
            )
        payload = response.json()
        if not isinstance(payload, Mapping) or payload.get("public") is True:
            raise PermissionError(f"storage bucket {self.bucket!r} is public")
        return dict(payload)

    def get(self, object_key: str) -> bytes:
        data = cached(self, object_key)
        if data is not None:
            return data
        response = self.client.get(self._url(object_key), headers=self._headers())
        response.raise_for_status()
        data = response.content
        # Only content-addressed, verified bytes may enter the session cache.
        parts = object_key.split('/')
        if len(parts) == 4 and parts[0] == 'sha256' and sha256_bytes(data) == parts[2]:
            remember(self, object_key, data)
        return data


class PostgresRepository:
    """Small synchronous repository with atomic leases and guarded completion."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connection: Any | None = None,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self.dsn = (
            dsn or os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
        )
        self._connection = connection
        self._connect_factory = connect
        self._pool = None
        self._pool_lock = Lock()
        if self._connection is None and self._connect_factory is None and not self.dsn:
            raise ValueError(
                "a Postgres DSN, connection, or connect factory is required"
            )

    def _new_connection(self) -> Any:
        if self._connect_factory is not None:
            return self._connect_factory()
        if self._connection is not None:
            return self._connection
        if psycopg is None:
            raise RuntimeError("psycopg is required for PostgresRepository")
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def _run(self, fn: Callable[[Any], Any]) -> Any:
        if self._connection is None and self._connect_factory is None:
            from psycopg_pool import ConnectionPool

            with self._pool_lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        self.dsn,
                        min_size=1,
                        max_size=4,
                        open=True,
                        kwargs={
                            "row_factory": dict_row,
                            "prepare_threshold": None,
                            "connect_timeout": 10,
                        },
                        timeout=30,
                    )
                    atexit.register(self.close)
            # The pool owns commit/rollback and returns the connection without
            # replaying an operation whose commit outcome might be unknown.
            with self._pool.connection() as conn:
                return fn(conn)
        conn = self._new_connection()
        owned = conn is not self._connection
        try:
            result = fn(conn)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            if owned:
                conn.close()

    def close(self) -> None:
        try:
            if self._pool is not None:
                self._pool.close()
        finally:
            atexit.unregister(self.close)

    @staticmethod
    def _one(cur: Any) -> dict[str, Any] | None:
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row) if not isinstance(row, dict) else row

    def create_batch(
        self,
        manifest: Any,
        config: Mapping[str, Any] | None = None,
        *,
        status: str = "pending",
    ) -> dict[str, Any]:
        manifest_hash = sha256_bytes(canonical_json_bytes(manifest))
        config_value = dict(config or {})
        config_hash = sha256_bytes(canonical_json_bytes(config_value))

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """INSERT INTO ingestion.batches
                    (manifest, manifest_hash, config, config_hash, status)
                    VALUES (%s::jsonb, %s, %s::jsonb, %s, %s)
                    RETURNING *""",
                    (
                        _json(manifest),
                        manifest_hash,
                        _json(config_value),
                        config_hash,
                        status,
                    ),
                )
                return self._one(cur) or {}

        return self._run(op)

    def register_input(
        self,
        batch_id: str | uuid.UUID,
        relative_path: str,
        *,
        file_name: str | None = None,
        content_hash: str | None = None,
        object_key: str | None = None,
        size_bytes: int | None = None,
    ) -> dict[str, Any]:
        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """INSERT INTO ingestion.inputs
                    (batch_id, relative_path, file_name, content_hash, object_key, size_bytes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (batch_id, relative_path) DO UPDATE SET
                      file_name = COALESCE(EXCLUDED.file_name, ingestion.inputs.file_name),
                      content_hash = COALESCE(EXCLUDED.content_hash, ingestion.inputs.content_hash),
                      object_key = COALESCE(EXCLUDED.object_key, ingestion.inputs.object_key),
                      size_bytes = COALESCE(EXCLUDED.size_bytes, ingestion.inputs.size_bytes)
                    RETURNING *""",
                    (
                        _uuid(batch_id),
                        relative_path,
                        file_name or relative_path.rsplit("/", 1)[-1],
                        content_hash,
                        object_key,
                        size_bytes,
                    ),
                )
                return self._one(cur) or {}

        return self._run(op)

    def ensure_job(
        self,
        *,
        batch_id: str | uuid.UUID,
        input_id: str | uuid.UUID,
        stage: str,
        input_artifact_hash: str | None = None,
        reading_hash: str | None = None,
        provider: str,
        model: str,
        config_version: str | None = None,
        provider_revision: str | None = None,
        prompt_version: str | None = None,
        adapter_version: str | None = None,
        schema_hash: str | None = None,
        settings: Mapping[str, Any] | None = None,
        work_key: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if work_key is None:
            key_value = {
                "input": input_artifact_hash,
                "reading": reading_hash,
                "stage": stage,
                "provider": provider,
                "model": model,
                "provider_revision": provider_revision or "unknown",
                "config": config_version,
                "prompt": prompt_version,
                "adapter": adapter_version,
                "schema": schema_hash,
                "settings": dict(settings or {}),
            }
            work_key = sha256_bytes(canonical_json_bytes(key_value))

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                # An explicit retry creates a work-key generation. Resume and
                # normal calls must pick the newest generation instead of
                # returning the failed historical base job.
                cur.execute(
                    """SELECT * FROM ingestion.jobs
                    WHERE work_key = %s OR work_key LIKE %s
                    ORDER BY created_at DESC LIMIT 1""",
                    (work_key, f"{work_key}:retry:%"),
                )
                existing = self._one(cur)
                if existing is not None:
                    return existing
                cur.execute(
                    """INSERT INTO ingestion.jobs
                    (batch_id, input_id, stage, input_artifact_hash, reading_hash,
                     provider, model, provider_revision, config_version, prompt_version,
                     adapter_version, schema_hash, settings, work_key, max_attempts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s, %s)
                    ON CONFLICT (work_key) DO UPDATE SET updated_at = now()
                    RETURNING *""",
                    (
                        _uuid(batch_id),
                        _uuid(input_id),
                        stage,
                        input_artifact_hash,
                        reading_hash,
                        provider,
                        model,
                        provider_revision,
                        config_version,
                        prompt_version,
                        adapter_version,
                        schema_hash,
                        _json(dict(settings or {})),
                        work_key,
                        max_attempts,
                    ),
                )
                return self._one(cur) or {}

        return self._run(op)

    def claim(
        self,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        job_id: str | uuid.UUID | None = None,
        stage: str | None = None,
    ) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        owner = worker_id or f"worker-{secrets.token_hex(8)}"

        def op(conn: Any) -> dict[str, Any] | None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """WITH candidate AS (
                      SELECT id FROM ingestion.jobs
                      WHERE state IN ('pending', 'retry_wait')
                        AND next_attempt_at <= now()
                        AND attempt_count < max_attempts
                        AND (%s::uuid IS NULL OR id = %s::uuid)
                        AND (%s::text IS NULL OR stage = %s::text)
                      ORDER BY id
                      LIMIT 1
                      FOR UPDATE SKIP LOCKED
                    )
                    UPDATE ingestion.jobs j
                    SET state = 'running', lease_owner = %s,
                        lease_token = gen_random_uuid(),
                        lease_expires_at = now() + (%s * interval '1 second'),
                        updated_at = now()
                    FROM candidate c WHERE j.id = c.id
                    RETURNING j.*""",
                    (_uuid(job_id), _uuid(job_id), stage, stage, owner, lease_seconds),
                )
                return self._one(cur)

        return self._run(op)

    def begin_attempt(
        self,
        job_id: str | uuid.UUID,
        claim_token: str | uuid.UUID,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """WITH guarded AS (
                      UPDATE ingestion.jobs SET attempt_count = attempt_count + 1,
                        updated_at = now()
                      WHERE id = %s AND state = 'running' AND lease_token = %s
                        AND lease_expires_at > now() AND attempt_count < max_attempts
                      RETURNING id, attempt_count
                    )
                    INSERT INTO ingestion.attempts (job_id, attempt_number, provider_request_id, status)
                    SELECT id, attempt_count, %s, 'running' FROM guarded
                    RETURNING *""",
                    (_uuid(job_id), _uuid(claim_token), request_id),
                )
                row = self._one(cur)
                if row is None:
                    raise PermissionError("job is not owned by this lease")
                return row

        return self._run(op)

    def claim_recovery(
        self,
        job_id: str | uuid.UUID,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Lease an unknown job only when its last external request is resumable."""
        owner = worker_id or f"worker-{secrets.token_hex(8)}"

        def op(conn: Any) -> dict[str, Any] | None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """UPDATE ingestion.jobs j SET state = 'running', lease_owner = %s,
                      lease_token = gen_random_uuid(), lease_expires_at = now() + (%s * interval '1 second'), updated_at = now()
                    WHERE j.id = %s AND j.state = 'unknown'
                      AND EXISTS (
                        SELECT 1 FROM ingestion.attempts a
                        WHERE a.job_id = j.id AND a.provider_request_id IS NOT NULL
                          AND NOT EXISTS (
                            SELECT 1 FROM ingestion.attempts later
                            WHERE later.job_id = a.job_id AND later.attempt_number > a.attempt_number
                          )
                      )
                    RETURNING j.*""",
                    (owner, lease_seconds, _uuid(job_id)),
                )
                return self._one(cur)

        return self._run(op)

    def record_request_id(
        self, attempt_id: str | uuid.UUID, request_id: str
    ) -> dict[str, Any]:
        return self._update_attempt(attempt_id, {"provider_request_id": request_id})

    def save_artifact(
        self,
        ref: StorageRef | Mapping[str, Any],
        *,
        payload: Any | None = None,
        parent_artifact_ids: Iterable[str | uuid.UUID] = (),
    ) -> dict[str, Any]:
        data = asdict(ref) if isinstance(ref, StorageRef) else dict(ref)
        digest = data.get("sha256")
        object_key = data.get("object_key")
        if not digest or not object_key:
            raise ValueError(
                "verified storage reference requires sha256 and object_key"
            )

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """INSERT INTO ingestion.artifacts
                    (sha256, kind, object_key, content_type, byte_size, payload, parent_artifact_ids, verified_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, now())
                    ON CONFLICT (sha256, kind) DO UPDATE SET updated_at = now()
                    RETURNING *""",
                    (
                        digest,
                        data.get("kind", "artifact"),
                        object_key,
                        data.get("content_type", "application/octet-stream"),
                        int(data.get("byte_size", 0)),
                        _json(payload) if payload is not None else None,
                        [str(v) for v in parent_artifact_ids],
                    ),
                )
                return self._one(cur) or {}

        return self._run(op)

    def artifacts_by_keys(self, keys):
        def op(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT * FROM ingestion.artifacts WHERE object_key = ANY(%s::text[])', (list(keys),))
                return [dict(row) for row in cur.fetchall()]
        return self._run(op)

    def complete(
        self,
        job_id: str | uuid.UUID,
        claim_token: str | uuid.UUID,
        artifact_id: str | uuid.UUID,
        *,
        status: str = "succeeded",
        result_status: str | None = None,
        interpreter: str | None = None,
        error: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "needs_review"}:
            raise ValueError("completion status must be succeeded or needs_review")

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """WITH completed AS (
                      UPDATE ingestion.jobs SET state = %s, artifact_id = %s,
                        last_error = NULL, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, updated_at = now()
                      WHERE id = %s AND lease_token = %s AND state = 'running'
                        AND lease_expires_at > now()
                        AND EXISTS (SELECT 1 FROM ingestion.artifacts
                                    WHERE id = %s AND verified_at IS NOT NULL)
                      RETURNING *
                    ), finished_attempts AS (
                      UPDATE ingestion.attempts a SET status = %s, finished_at = now(), error = %s::jsonb
                      FROM completed j WHERE a.job_id = j.id
                        AND (a.status = 'running' OR (a.status = 'unknown' AND a.attempt_number =
                          (SELECT max(attempt_number) FROM ingestion.attempts WHERE job_id = j.id)))
                      RETURNING a.id
                    ) SELECT * FROM completed""",
                    (status, _uuid(artifact_id), _uuid(job_id), _uuid(claim_token),
                     _uuid(artifact_id), status, _json(error) if error else None),
                )
                result = self._one(cur)
                if result is None:
                    cur.execute(
                        "SELECT id FROM ingestion.jobs WHERE id = %s AND lease_token = %s "
                        "AND state = 'running' AND lease_expires_at > now()",
                        (_uuid(job_id), _uuid(claim_token)),
                    )
                    if self._one(cur) is None:
                        raise PermissionError("job is not owned by this lease")
                    raise ValueError("artifact does not exist or is not verified")
                job = result
                if interpreter:
                    persisted_result_status = result_status or (
                        "completed" if status == "succeeded" else status
                    )
                    cur.execute(
                        """INSERT INTO ingestion.input_results (input_id, interpreter, job_id, artifact_id, status, error)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (input_id, interpreter) DO UPDATE SET job_id = EXCLUDED.job_id,
                          artifact_id = EXCLUDED.artifact_id, status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()""",
                        (
                            job["input_id"],
                            interpreter,
                            _uuid(job_id),
                            _uuid(artifact_id),
                            persisted_result_status,
                            _json(error) if error else None,
                        ),
                    )
                return result

        return self._run(op)

    def fail(
        self,
        job_id: str | uuid.UUID,
        claim_token: str | uuid.UUID,
        error: Mapping[str, Any] | str,
        *,
        retryable: bool = True,
        unknown: bool = False,
        retry_after_seconds: int = 0,
    ) -> dict[str, Any]:
        state = "unknown" if unknown else ("retry_wait" if retryable else "failed")
        error_payload = error if isinstance(error, Mapping) else {"message": error}

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """UPDATE ingestion.jobs SET state = CASE WHEN %s = 'retry_wait' AND attempt_count >= max_attempts THEN 'failed' ELSE %s END,
                      last_error = %s::jsonb, next_attempt_at = now() + (%s * interval '1 second'),
                      lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, updated_at = now()
                      WHERE id = %s AND state = 'running' AND lease_token = %s RETURNING *""",
                    (
                        state,
                        state,
                        _json(error_payload),
                        max(0, retry_after_seconds),
                        _uuid(job_id),
                        _uuid(claim_token),
                    ),
                )
                row = self._one(cur)
                if row is None:
                    raise PermissionError("job is not owned by this lease")
                cur.execute(
                    "UPDATE ingestion.attempts SET status = %s, finished_at = now(), error = %s::jsonb WHERE job_id = %s AND status = 'running'",
                    (
                        "unknown" if unknown else "failed",
                        _json(error_payload),
                        _uuid(job_id),
                    ),
                )
                return row

        return self._run(op)

    def _update_attempt(
        self, attempt_id: str | uuid.UUID, values: Mapping[str, Any]
    ) -> dict[str, Any]:
        if set(values) != {"provider_request_id"}:
            raise ValueError("unsupported attempt update")

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "UPDATE ingestion.attempts SET provider_request_id = %s WHERE id = %s RETURNING *",
                    (values["provider_request_id"], _uuid(attempt_id)),
                )
                row = self._one(cur)
                if row is None:
                    raise KeyError(f"attempt {attempt_id} not found")
                return row

        return self._run(op)

    def reconcile_expired(
        self, batch_id: str | uuid.UUID | None = None
    ) -> list[dict[str, Any]]:
        """Mark expired leases unknown; never silently resubmit an external call."""

        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """UPDATE ingestion.jobs j SET state = 'unknown', lease_owner = NULL,
                      lease_token = NULL, lease_expires_at = NULL,
                      last_error = jsonb_build_object('code', 'lease_expired',
                        'message', 'External outcome must be reconciled before retry'), updated_at = now()
                      WHERE state = 'running' AND lease_expires_at < now()
                        AND (%s::uuid IS NULL OR batch_id = %s::uuid)
                      RETURNING j.*""",
                    (_uuid(batch_id), _uuid(batch_id)),
                )
                rows = [dict(row) for row in cur.fetchall()]
                for row in rows:
                    cur.execute(
                        "UPDATE ingestion.attempts SET status = 'unknown', finished_at = now(), error = %s::jsonb WHERE job_id = %s AND status = 'running'",
                        (
                            _json(
                                {
                                    "code": "lease_expired",
                                    "message": "External outcome must be reconciled before retry",
                                }
                            ),
                            row["id"],
                        ),
                    )
                return rows

        return self._run(op)

    def list_attempts(self, job_id: str | uuid.UUID) -> list[dict[str, Any]]:
        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT * FROM ingestion.attempts WHERE job_id = %s ORDER BY attempt_number",
                    (_uuid(job_id),),
                )
                return [dict(row) for row in cur.fetchall()]

        return self._run(op)

    def record_attempt_artifact(
        self, attempt_id: str | uuid.UUID, artifact_id: str | uuid.UUID
    ) -> dict[str, Any]:
        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """UPDATE ingestion.attempts
                    SET raw_artifact_ids = raw_artifact_ids || %s::jsonb
                    WHERE id = %s RETURNING *""",
                    (_json([str(artifact_id)]), _uuid(attempt_id)),
                )
                row = self._one(cur)
                if row is None:
                    raise KeyError(f"attempt {attempt_id} not found")
                return row

        return self._run(op)

    def finish_attempt_metadata(
        self,
        attempt_id: str | uuid.UUID,
        *,
        usage: Mapping[str, Any] | None = None,
        raw_artifact_ids: Iterable[str | uuid.UUID] = (),
        latency_seconds: float | None = None,
        request_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """UPDATE ingestion.attempts SET usage = COALESCE(%s::jsonb, usage),
                      raw_artifact_ids = raw_artifact_ids || %s::jsonb,
                      latency_seconds = COALESCE(%s, latency_seconds),
                      request_metadata = COALESCE(%s::jsonb, request_metadata)
                      WHERE id = %s RETURNING *""",
                    (
                        _json(usage) if usage is not None else None,
                        _json([str(value) for value in raw_artifact_ids]),
                        latency_seconds,
                        _json(request_metadata)
                        if request_metadata is not None
                        else None,
                        _uuid(attempt_id),
                    ),
                )
                row = self._one(cur)
                if row is None:
                    raise KeyError(f"attempt {attempt_id} not found")
                return row

        return self._run(op)

    def retry_jobs(
        self, batch_id: str | uuid.UUID, stage: str, *, include_unknown: bool = False,
        input_id: str | uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Create a new, auditable generation for explicitly selected jobs."""
        allowed = (
            ("failed", "needs_review", "unknown")
            if include_unknown
            else ("failed", "needs_review")
        )

        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """INSERT INTO ingestion.jobs
                    (batch_id, input_id, stage, input_artifact_hash, reading_hash,
                     provider, model, provider_revision, config_version, prompt_version,
                     adapter_version, schema_hash, settings, work_key, max_attempts)
                    SELECT batch_id, input_id, stage, input_artifact_hash, reading_hash,
                      provider, model, provider_revision, config_version, prompt_version,
                      adapter_version, schema_hash, settings,
                      work_key || ':retry:' || gen_random_uuid()::text, max_attempts
                    FROM ingestion.jobs
                    WHERE batch_id = %s AND stage = %s AND state = ANY(%s::text[])
                      AND (%s::uuid IS NULL OR input_id = %s::uuid)
                      AND NOT EXISTS (
                        SELECT 1 FROM ingestion.jobs newer
                        WHERE newer.work_key LIKE ingestion.jobs.work_key || ':retry:%%'
                      )
                    RETURNING *""",
                    (_uuid(batch_id), stage, list(allowed), _uuid(input_id), _uuid(input_id)),
                )
                return [dict(row) for row in cur.fetchall()]

        return self._run(op)

    def list_inputs(self, batch_id: str | uuid.UUID) -> list[dict[str, Any]]:
        return self._list("inputs", "batch_id", batch_id, "relative_path")

    def list_jobs(self, batch_id: str | uuid.UUID | None) -> list[dict[str, Any]]:
        if batch_id is None:

            def op(conn: Any) -> list[dict[str, Any]]:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT * FROM ingestion.jobs ORDER BY created_at")
                    return [dict(row) for row in cur.fetchall()]

            return self._run(op)
        return self._list("jobs", "batch_id", batch_id, "created_at")

    def _list(
        self, table: str, column: str, value: str | uuid.UUID, order: str
    ) -> list[dict[str, Any]]:
        if table not in {"inputs", "jobs"}:
            raise ValueError("invalid table")

        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT * FROM ingestion.{table} WHERE {column} = %s ORDER BY {order}",
                    (_uuid(value),),
                )
                return [dict(row) for row in cur.fetchall()]

        return self._run(op)

    def set_result(
        self,
        input_id: str | uuid.UUID,
        interpreter: str,
        artifact_id: str | uuid.UUID | None,
        status: str,
        *,
        error: Mapping[str, Any] | None = None,
        job_id: str | uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if status not in {"completed", "needs_review", "failed", "unknown"}:
            raise ValueError("invalid result status")

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """INSERT INTO ingestion.input_results (input_id, interpreter, job_id, artifact_id, status, error)
                    VALUES (%s, %s, COALESCE(%s, (SELECT id FROM ingestion.jobs WHERE input_id = %s ORDER BY created_at DESC LIMIT 1)), %s, %s, %s::jsonb)
                    ON CONFLICT (input_id, interpreter) DO UPDATE SET job_id = COALESCE(EXCLUDED.job_id, ingestion.input_results.job_id),
                      artifact_id = EXCLUDED.artifact_id, status = EXCLUDED.status, error = EXCLUDED.error, updated_at = now()
                    RETURNING *""",
                    (
                        _uuid(input_id),
                        interpreter,
                        _uuid(job_id),
                        _uuid(input_id),
                        _uuid(artifact_id),
                        status,
                        _json(error) if error else None,
                    ),
                )
                row = self._one(cur)
                if row is None:
                    raise ValueError("input has no job to attach result")
                return row

        return self._run(op)

    def set_batch_status(
        self, batch_id: str | uuid.UUID, status: str
    ) -> dict[str, Any]:
        if status not in {
            "pending",
            "running",
            "partial",
            "completed",
            "succeeded",
            "failed",
        }:
            raise ValueError("invalid batch status")

        def op(conn: Any) -> dict[str, Any]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "UPDATE ingestion.batches SET status = %s, updated_at = now() WHERE id = %s RETURNING *",
                    (status, _uuid(batch_id)),
                )
                row = self._one(cur)
                if row is None:
                    raise KeyError(f"batch {batch_id} not found")
                return row

        return self._run(op)

    def get_batch(self, batch_id: str | uuid.UUID) -> dict[str, Any] | None:
        return self._get("batches", batch_id)

    def get_input(self, input_id: str | uuid.UUID) -> dict[str, Any] | None:
        return self._get("inputs", input_id)

    def get_job(self, job_id: str | uuid.UUID) -> dict[str, Any] | None:
        return self._get("jobs", job_id)

    def get_artifact(self, artifact_id: str | uuid.UUID) -> dict[str, Any] | None:
        return self._get("artifacts", artifact_id)

    def _get(self, table: str, value: str | uuid.UUID) -> dict[str, Any] | None:
        if table not in {"batches", "inputs", "jobs", "artifacts"}:
            raise ValueError("invalid table")

        def op(conn: Any) -> dict[str, Any] | None:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT * FROM ingestion.{table} WHERE id = %s", (_uuid(value),)
                )
                return self._one(cur)

        return self._run(op)

    def results(self, batch_id: str | uuid.UUID) -> list[dict[str, Any]]:
        def op(conn: Any) -> list[dict[str, Any]]:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT r.*, i.relative_path, i.file_name FROM ingestion.input_results r
                    JOIN ingestion.inputs i ON i.id = r.input_id
                    WHERE i.batch_id = %s ORDER BY i.relative_path, r.interpreter""",
                    (_uuid(batch_id),),
                )
                return [dict(row) for row in cur.fetchall()]

        return self._run(op)


__all__ = [
    "PostgresRepository",
    "StorageRef",
    "SupabaseStorage",
    "canonical_json_bytes",
    "sha256_bytes",
]
