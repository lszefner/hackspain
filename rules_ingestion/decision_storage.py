from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from ingestion.storage import (
    PostgresRepository,
    SupabaseStorage,
    canonical_json_bytes,
    sha256_bytes,
)

from .decision_context import (
    ContextBundle,
    ContextError,
    evaluate_context,
    load_schema,
    validate_context,
)

CONTEXT_ID_PATTERN = re.compile(r"^dc_[a-f0-9]{64}$")
SCHEMA_PATH = Path(__file__).resolve().parent / "decision_context.schema.json"

_SELECT_CONTEXT = """
SELECT d.*, c.object_key AS context_object_key,
       s.object_key AS schema_object_key,
       e.object_key AS evaluation_object_key,
       e.sha256 AS evaluation_sha256
FROM ingestion.decision_contexts d
JOIN ingestion.artifacts c ON c.id = d.context_artifact_id
JOIN ingestion.artifacts s ON s.id = d.schema_artifact_id
JOIN ingestion.artifacts e ON e.id = d.evaluation_artifact_id
WHERE d.context_id = %s
"""

_INSERT_CONTEXT = """
INSERT INTO ingestion.decision_contexts
(context_id,file_id,context_artifact_id,schema_artifact_id,evaluation_artifact_id,context_sha256,schema_sha256,evaluation_date,data_status,execution_status,decision)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (context_id) DO NOTHING
"""

_COMPARED_COLUMNS = (
    "file_id", "context_artifact_id", "schema_artifact_id",
    "evaluation_artifact_id", "context_sha256", "schema_sha256",
    "evaluation_date", "data_status", "execution_status", "decision",
)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool) or value is None \
            or isinstance(value, (str, int, float)):
        return value
    raise ContextError(
        f"unsupported value type for persistence: {type(value).__name__}")


def _normalized(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


class DecisionRepository(PostgresRepository):
    def record_context(self, values: dict) -> dict:
        requested = {key: values[key] for key in
                     ("context_id",) + _COMPARED_COLUMNS}

        def op(conn):
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    _INSERT_CONTEXT,
                    (
                        values["context_id"],
                        values["file_id"],
                        _norm_uuid(values["context_artifact_id"]),
                        _norm_uuid(values["schema_artifact_id"]),
                        _norm_uuid(values["evaluation_artifact_id"]),
                        values["context_sha256"],
                        values["schema_sha256"],
                        values["evaluation_date"],
                        values["data_status"],
                        values["execution_status"],
                        values["decision"],
                    ),
                )
                cur.execute(_SELECT_CONTEXT, (values["context_id"],))
                row = cur.fetchone()
            if row is None:
                raise ContextError(
                    "decision context index row missing after insert")
            stored = dict(row)
            for key in _COMPARED_COLUMNS:
                if _normalized(stored.get(key)) != _normalized(requested[key]):
                    raise ContextError(
                        f"conflicting stored decision context for "
                        f"{values['context_id']} (column {key})")
            return stored

        return self._run(op)

    def get_context(self, context_id: str) -> dict | None:
        if not CONTEXT_ID_PATTERN.match(context_id):
            raise ContextError(f"invalid context_id {context_id!r}")

        def op(conn):
            from psycopg.rows import dict_row

            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_SELECT_CONTEXT, (context_id,))
                row = cur.fetchone()
                return dict(row) if row is not None else None

        return self._run(op)


def _norm_uuid(value):
    return str(value) if value is not None else None


class DecisionStore:
    def __init__(self, storage, *, repository: DecisionRepository | None = None,
                 index_dir: Path | None = None):
        if (repository is None) == (index_dir is None):
            raise ValueError(
                "exactly one index backend is required: repository or index_dir")
        self.storage = storage
        self.repository = repository
        self.index_dir = Path(index_dir) if index_dir is not None else None
        if self.index_dir is not None:
            self.index_dir.mkdir(parents=True, exist_ok=True)
        self.backend = "supabase" if repository is not None else "local"

    def _put_verified(self, data: bytes, kind: str,
                      content_type: str = "application/octet-stream"):
        expected = sha256_bytes(data)
        ref = self.storage.put(data, kind, content_type)
        if ref.sha256 != expected or int(ref.byte_size) != len(data):
            raise ContextError(
                f"storage reference mismatch for uploaded {kind}")
        fetched = self.storage.get(ref.object_key)
        if sha256_bytes(fetched) != expected or len(fetched) != len(data):
            raise ContextError(
                f"stored artifact verification failed for {ref.object_key}")
        return ref

    def _publish_receipt(self, receipt: dict) -> dict:
        if self.repository is not None:
            row = self.repository.record_context(receipt)
            merged = dict(receipt)
            for key in ("context_artifact_id", "schema_artifact_id",
                        "evaluation_artifact_id"):
                merged[key] = str(row[key])
            merged["created_at"] = _normalized(row.get("created_at"))
            merged["context_object_key"] = row["context_object_key"]
            merged["schema_object_key"] = row["schema_object_key"]
            merged["evaluation_object_key"] = row["evaluation_object_key"]
            merged["evaluation_sha256"] = row["evaluation_sha256"]
            return _jsonable(merged)
        target = self.index_dir / f"{receipt['context_id']}.json"
        payload = canonical_json_bytes(_jsonable(receipt))
        fd, tmp_name = tempfile.mkstemp(dir=self.index_dir, prefix=".receipt-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp_name, target)
            except FileExistsError:
                existing = target.read_bytes()
                if existing != payload:
                    raise ContextError(
                        f"conflicting stored receipt for "
                        f"{receipt['context_id']}")
            try:
                dir_fd = os.open(self.index_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
            return json.loads(payload)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def save(self, bundle: ContextBundle) -> dict:
        context = bundle.context
        validate_context(context, bundle.artifacts)
        evaluation = evaluate_context(bundle)

        artifact_ids: dict[str, str] = {}
        source_artifact_ids = []
        for source_id, source in context["sources"].items():
            ref_name = source["artifact_ref"]
            if ref_name is None:
                continue
            data = bundle.artifacts[ref_name]
            ref = self._put_verified(
                data, f"decision-source-{source['kind']}")
            if ref.object_key != ref_name or ref.sha256 != source["sha256"]:
                raise ContextError(
                    f"storage reference mismatch for source {source_id}")
            if self.repository is not None:
                payload = None
                try:
                    payload = json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = None
                row = self.repository.save_artifact(ref, payload=payload)
                artifact_ids[source_id] = str(row["id"])
                source_artifact_ids.append(row["id"])

        schema_bytes = SCHEMA_PATH.read_bytes()
        schema_sha = sha256_bytes(schema_bytes)
        schema_ref = self._put_verified(
            schema_bytes, "decision-context-schema", "application/json")
        schema_artifact_id = None
        if self.repository is not None:
            row = self.repository.save_artifact(
                schema_ref, payload=json.loads(schema_bytes))
            schema_artifact_id = row["id"]

        context_bytes = canonical_json_bytes(_jsonable(context))
        context_sha = sha256_bytes(context_bytes)
        context_ref = self._put_verified(
            context_bytes, "decision-context", "application/json")
        context_artifact_id = None
        if self.repository is not None:
            row = self.repository.save_artifact(
                context_ref, payload=_jsonable(context),
                parent_artifact_ids=[*source_artifact_ids,
                                     schema_artifact_id])
            context_artifact_id = row["id"]

        evaluation_bytes = canonical_json_bytes(_jsonable(evaluation))
        evaluation_sha = sha256_bytes(evaluation_bytes)
        evaluation_ref = self._put_verified(
            evaluation_bytes, "decision-evaluation", "application/json")
        evaluation_artifact_id = None
        if self.repository is not None:
            row = self.repository.save_artifact(
                evaluation_ref, payload=_jsonable(evaluation),
                parent_artifact_ids=[context_artifact_id])
            evaluation_artifact_id = row["id"]

        receipt = {
            "backend": self.backend,
            "context_id": context["context_id"],
            "file_id": context["file_id"],
            "context_sha256": context_sha,
            "context_object_key": context_ref.object_key,
            "schema_sha256": schema_sha,
            "schema_object_key": schema_ref.object_key,
            "evaluation_sha256": evaluation_sha,
            "evaluation_object_key": evaluation_ref.object_key,
            "evaluation_date": context["evaluation_date"],
            "data_status": context["preflight"]["data_status"],
            "execution_status": context["preflight"]["execution_status"],
            "decision": evaluation["decision"],
        }
        if self.repository is not None:
            receipt["context_artifact_id"] = str(context_artifact_id)
            receipt["schema_artifact_id"] = str(schema_artifact_id)
            receipt["evaluation_artifact_id"] = str(evaluation_artifact_id)
        return self._publish_receipt(receipt)

    def _receipt_for(self, context_id: str) -> dict:
        if not CONTEXT_ID_PATTERN.match(context_id):
            raise ContextError(f"invalid context_id {context_id!r}")
        if self.repository is not None:
            row = self.repository.get_context(context_id)
            if row is None:
                raise KeyError(f"unknown decision context {context_id}")
            return _jsonable(row)
        target = self.index_dir / f"{context_id}.json"
        if not target.exists():
            raise KeyError(f"unknown decision context {context_id}")
        try:
            return json.loads(target.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextError("stored receipt is not valid JSON") from exc

    @staticmethod
    def _expected_key(sha256: Any, kind: str) -> str:
        if not isinstance(sha256, str) \
                or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ContextError("stored receipt carries an invalid digest")
        return f"sha256/{sha256[:2]}/{sha256}/{kind}"

    def load(self, context_id: str) -> ContextBundle:
        receipt = self._receipt_for(context_id)
        if receipt.get("context_id") != context_id:
            raise ContextError("stored receipt does not match context_id")
        context_key = self._expected_key(
            receipt.get("context_sha256"), "decision-context")
        schema_key = self._expected_key(
            receipt.get("schema_sha256"), "decision-context-schema")
        evaluation_key = self._expected_key(
            receipt.get("evaluation_sha256"), "decision-evaluation")
        if receipt.get("context_object_key") != context_key \
                or receipt.get("schema_object_key") != schema_key \
                or receipt.get("evaluation_object_key") != evaluation_key:
            raise ContextError("stored receipt object keys are inconsistent")
        context_data = self.storage.get(context_key)
        if sha256_bytes(context_data) != receipt["context_sha256"]:
            raise ContextError("stored context bytes fail digest verification")
        schema_data = self.storage.get(schema_key)
        if sha256_bytes(schema_data) != receipt["schema_sha256"]:
            raise ContextError("stored schema bytes fail digest verification")
        current_schema = SCHEMA_PATH.read_bytes()
        if sha256_bytes(current_schema) != receipt["schema_sha256"]:
            raise ContextError(
                "stored context was written against a different schema version")
        evaluation_data = self.storage.get(evaluation_key)
        if sha256_bytes(evaluation_data) != receipt["evaluation_sha256"]:
            raise ContextError(
                "stored evaluation bytes fail digest verification")
        try:
            context = json.loads(context_data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextError("stored context is not valid JSON") from exc
        if context.get("context_id") != context_id:
            raise ContextError("stored context_id mismatch")
        try:
            Draft202012Validator(load_schema()).validate(context)
        except ValidationError as exc:
            raise ContextError("stored context fails schema validation") \
                from exc
        artifacts: dict[str, bytes] = {}
        for source in context["sources"].values():
            ref_name = source["artifact_ref"]
            if ref_name is None:
                continue
            expected = self._expected_key(
                source.get("sha256"), f"decision-source-{source['kind']}")
            if ref_name != expected:
                raise ContextError("stored source artifact key is inconsistent")
            data = self.storage.get(ref_name)
            if sha256_bytes(data) != source["sha256"]:
                raise ContextError(
                    "stored source bytes fail digest verification")
            artifacts[ref_name] = data
        bundle = ContextBundle(context=context, artifacts=artifacts)
        validate_context(context, artifacts)
        replayed_eval = evaluate_context(bundle)
        replayed = canonical_json_bytes(_jsonable(replayed_eval))
        if replayed != evaluation_data:
            raise ContextError(
                "stored evaluation does not match deterministic replay")
        for key, expected in (
                ("decision", replayed_eval["decision"]),
                ("evaluation_date", context["evaluation_date"]),
                ("file_id", context["file_id"]),
                ("data_status", context["preflight"]["data_status"]),
                ("execution_status", context["preflight"]["execution_status"])):
            if receipt.get(key) != expected:
                raise ContextError(
                    f"stored receipt {key} does not match context")
        return bundle

    def close(self):
        if self.repository is not None:
            self.repository.close()
        client = getattr(self.storage, "client", None)
        if client is not None and hasattr(client, "close"):
            client.close()


def create_decision_store(backend: str, *, local_root: Path) -> DecisionStore:
    if backend == "local":
        from webui.local_backend import LocalStorage

        root = Path(local_root)
        return DecisionStore(LocalStorage(root / "objects"),
                             index_dir=root / "contexts")
    if backend == "supabase":
        storage = SupabaseStorage(
            bucket=os.environ.get("SUPABASE_STORAGE_BUCKET",
                                  "invoice-ingestion-private"))
        try:
            repository = DecisionRepository()
        except Exception:
            client = getattr(storage, "client", None)
            if client is not None and hasattr(client, "close"):
                client.close()
            raise
        store = DecisionStore(storage, repository=repository)
        try:
            storage.preflight()
            repository.get_context("dc_" + "0" * 64)
        except Exception:
            store.close()
            raise
        return store
    raise ValueError(f"unknown decision backend {backend!r}")
