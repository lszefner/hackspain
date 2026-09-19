from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_decision_context import CAPTURED, EVAL_DATE, build

from ingestion.storage import (
    SupabaseStorage,
    canonical_json_bytes,
    sha256_bytes,
)
from rules_ingestion.decision_context import (
    ContextError,
    evaluate_context,
)
from rules_ingestion.decision_storage import (
    DecisionRepository,
    DecisionStore,
    create_decision_store,
)
from backend.local_backend import LocalStorage


def _local_store(root: Path) -> DecisionStore:
    return DecisionStore(LocalStorage(root / "objects"),
                         index_dir=root / "contexts")


def test_local_save_load_roundtrip(tmp_path):
    store = _local_store(tmp_path)
    bundle = build()
    receipt = store.save(bundle)
    assert receipt["backend"] == "local"
    assert receipt["context_id"] == bundle.context["context_id"]
    assert receipt["decision"] == "PAGAR"
    assert sha256_bytes(canonical_json_bytes(
        json.loads(canonical_json_bytes(bundle.context)))) \
        == receipt["context_sha256"]

    fresh = _local_store(tmp_path)
    loaded = fresh.load(receipt["context_id"])
    assert loaded.context == bundle.context
    assert evaluate_context(loaded) == evaluate_context(bundle)


def test_save_is_idempotent_and_dates_create_new_context(tmp_path):
    store = _local_store(tmp_path)
    bundle = build()
    first = store.save(bundle)
    second = store.save(bundle)
    assert first == second
    receipts = list((tmp_path / "contexts").glob("*.json"))
    assert len(receipts) == 1

    from test_decision_context import make_outcome, make_ruleset, make_snapshots

    import rules_ingestion.decision_context as dc

    bundle2 = dc.build_context(
        make_outcome(), snapshots=make_snapshots(), ruleset=make_ruleset(),
        evaluation_date="2026-09-20", captured_at=CAPTURED)
    receipt2 = store.save(bundle2)
    assert receipt2["context_id"] != first["context_id"]
    assert len(list((tmp_path / "contexts").glob("*.json"))) == 2
    assert EVAL_DATE != "2026-09-20"


def test_tampered_object_and_receipt_fail(tmp_path):
    store = _local_store(tmp_path)
    bundle = build()
    receipt = store.save(bundle)
    key = receipt["context_object_key"]
    target = tmp_path / "objects" / key
    target.write_bytes(b"tampered")
    with pytest.raises(ContextError):
        store.load(receipt["context_id"])

    store2 = _local_store(tmp_path / "b")
    store2.save(bundle)
    receipt_path = tmp_path / "b" / "contexts" / f"{receipt['context_id']}.json"
    data = json.loads(receipt_path.read_text())
    data["decision"] = "PAGAR" if data["decision"] != "PAGAR" else "ESCALAR"
    receipt_path.write_text(json.dumps(data))
    store3 = _local_store(tmp_path / "b")
    with pytest.raises(ContextError):
        store3.load(receipt["context_id"])


def test_conflicting_receipt_bytes_rejected(tmp_path):
    store = _local_store(tmp_path)
    bundle = build()
    receipt = store.save(bundle)
    receipt_path = tmp_path / "contexts" / f"{receipt['context_id']}.json"
    data = json.loads(receipt_path.read_text())
    data["file_id"] = "forged"
    receipt_path.write_text(json.dumps(data))
    with pytest.raises(ContextError):
        store.save(bundle)


def test_upload_failure_prevents_index(tmp_path):
    class FailingStorage(LocalStorage):
        def put(self, data, kind, content_type="application/octet-stream"):
            if kind == "decision-context":
                raise OSError("synthetic upload failure")
            return super().put(data, kind, content_type)

    store = DecisionStore(FailingStorage(tmp_path / "objects"),
                          index_dir=tmp_path / "contexts")
    with pytest.raises(IOError):
        store.save(build())
    assert not list((tmp_path / "contexts").glob("*.json"))


def test_unknown_and_invalid_context_ids(tmp_path):
    store = _local_store(tmp_path)
    with pytest.raises(KeyError):
        store.load("dc_" + "0" * 64)
    with pytest.raises(ContextError):
        store.load("../../etc/passwd")


class _Response:
    def __init__(self, content: bytes = b"", status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.posts: list[tuple[str, bytes]] = []

    def post(self, url: str, *, content: bytes, headers: dict) -> _Response:
        self.posts.append((url, content))
        self.objects.setdefault(url, content)
        return _Response(status_code=200)

    def get(self, url: str, *, headers: dict) -> _Response:
        return _Response(self.objects[url])


def test_supabase_save_load_and_index(postgres_dsn):
    import psycopg

    client = _Client()
    storage = SupabaseStorage(url="https://example.test", client=client)
    store = DecisionStore(
        storage, repository=DecisionRepository(postgres_dsn))
    bundle = build()
    receipt = store.save(bundle)
    assert receipt["backend"] == "supabase"
    assert receipt["decision"] == "PAGAR"
    with psycopg.connect(postgres_dsn) as conn:
        rows = conn.execute(
            "SELECT * FROM ingestion.decision_contexts").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == receipt["context_id"]
        rel = conn.execute(
            "SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace WHERE n.nspname='ingestion' "
            "AND c.relname='decision_contexts'").fetchone()
        assert rel[0] is True
    loaded = store.load(receipt["context_id"])
    assert loaded.context == bundle.context

    store.save(bundle)
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ingestion.decision_contexts").fetchone()[0] == 1

    from test_decision_context import make_outcome, make_ruleset, make_snapshots

    import rules_ingestion.decision_context as dc

    snapshots = make_snapshots(supplier_records=[{
        "id": "P999", "nif": "B12345678",
        "iban": "ES9121000418450200051332"}])
    bundle2 = dc.build_context(
        make_outcome(), snapshots=snapshots, ruleset=make_ruleset(),
        evaluation_date=EVAL_DATE, captured_at=CAPTURED)
    store.save(bundle2)
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ingestion.decision_contexts").fetchone()[0] == 2
    store.close()


def test_decision_contexts_rls_denies_frontend_role(postgres_dsn):
    import psycopg

    client = _Client()
    storage = SupabaseStorage(url="https://example.test", client=client)
    store = DecisionStore(storage, repository=DecisionRepository(postgres_dsn))
    store.save(build())
    store.close()

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ingestion.decision_contexts"
        ).fetchone()[0] >= 1
        conn.execute("DROP ROLE IF EXISTS decision_frontend_test")
        conn.execute("CREATE ROLE decision_frontend_test NOLOGIN")
        conn.execute(
            "GRANT USAGE ON SCHEMA ingestion TO decision_frontend_test")
        conn.execute(
            "GRANT SELECT ON ingestion.decision_contexts "
            "TO decision_frontend_test")
        conn.execute("SET ROLE decision_frontend_test")
        assert conn.execute(
            "SELECT count(*) FROM ingestion.decision_contexts").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO ingestion.decision_contexts "
                "(context_id, file_id, context_artifact_id, "
                "schema_artifact_id, evaluation_artifact_id, "
                "context_sha256, schema_sha256, evaluation_date, "
                "data_status, execution_status, decision) VALUES "
                "('dc_" + "1" * 64 + "', 'f', gen_random_uuid(), "
                "gen_random_uuid(), gen_random_uuid(), '" + "0" * 64 +
                "', '" + "0" * 64 + "', '2026-09-19', 'ready', "
                "'supported', 'PAGAR')")
        conn.execute("RESET ROLE")
        conn.execute(
            "REVOKE ALL ON ingestion.decision_contexts "
            "FROM decision_frontend_test")
        conn.execute(
            "REVOKE ALL ON SCHEMA ingestion FROM decision_frontend_test")
        conn.execute("DROP ROLE decision_frontend_test")


def test_schema_version_mismatch_rejected(tmp_path, monkeypatch):
    store = _local_store(tmp_path)
    bundle = build()
    receipt = store.save(bundle)
    import rules_ingestion.decision_storage as ds

    original = ds.SCHEMA_PATH
    monkeypatch.setattr(ds, "SCHEMA_PATH",
                        tmp_path / "other-schema.json")
    (tmp_path / "other-schema.json").write_bytes(b'{"x":1}')
    with pytest.raises(ContextError):
        store.load(receipt["context_id"])
    monkeypatch.setattr(ds, "SCHEMA_PATH", original)
    assert store.load(receipt["context_id"]).context == bundle.context


def test_create_decision_store_local_and_unknown(tmp_path):
    store = create_decision_store("local", local_root=tmp_path / "d")
    assert store.backend == "local"
    store.close()
    with pytest.raises(ValueError):
        create_decision_store("bogus", local_root=tmp_path / "d")


def test_put_verified_rejects_self_consistent_wrong_bytes(tmp_path):
    from ingestion.storage import StorageRef

    class WrongStorage(LocalStorage):
        def put(self, data, kind, content_type="application/octet-stream"):
            wrong = bytes(b ^ 0xFF for b in data[:8]) + data[8:]
            digest = sha256_bytes(wrong)
            object_key = f"sha256/{digest[:2]}/{digest}/{kind}"
            path = self.root / object_key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wrong)
            return StorageRef(digest, object_key, len(wrong), content_type,
                              kind)

    store = DecisionStore(WrongStorage(tmp_path / "objects"),
                          index_dir=tmp_path / "contexts")
    with pytest.raises(ContextError):
        store.save(build())
    assert not list((tmp_path / "contexts").glob("*.json"))


def test_receipt_binding_and_key_validation(tmp_path):
    from test_decision_context import make_outcome, make_ruleset, make_snapshots

    import rules_ingestion.decision_context as dc

    store = _local_store(tmp_path)
    bundle_a = build()
    bundle_b = dc.build_context(
        make_outcome(), snapshots=make_snapshots(), ruleset=make_ruleset(),
        evaluation_date="2026-09-20", captured_at=CAPTURED)
    receipt_a = store.save(bundle_a)
    receipt_b = store.save(bundle_b)
    path_b = tmp_path / "contexts" / f"{receipt_b['context_id']}.json"
    path_a = tmp_path / "contexts" / f"{receipt_a['context_id']}.json"
    path_a.write_bytes(path_b.read_bytes())
    with pytest.raises(ContextError):
        store.load(receipt_a["context_id"])

    path_a.write_text(json.dumps({
        **json.loads(path_b.read_text()),
        "context_id": receipt_a["context_id"],
        "context_object_key": "../../escape",
    }))
    with pytest.raises(ContextError):
        store.load(receipt_a["context_id"])

    crafted = json.loads(path_b.read_text())
    crafted["context_id"] = receipt_a["context_id"]
    crafted["schema_object_key"] = crafted["context_object_key"]
    path_a.write_text(json.dumps(crafted))
    with pytest.raises(ContextError):
        store.load(receipt_a["context_id"])


def test_create_decision_store_supabase_bucket_env(tmp_path, monkeypatch):
    import rules_ingestion.decision_storage as ds

    calls = {}

    class FakeStorage:
        def __init__(self, **kwargs):
            calls["bucket"] = kwargs.get("bucket")
            self.client = None

        def preflight(self):
            calls["preflight"] = True

    class FakeRepo:
        def __init__(self):
            calls["repo"] = True

        def get_context(self, context_id):
            calls["probe"] = context_id

        def close(self):
            pass

    monkeypatch.setattr(ds, "SupabaseStorage", FakeStorage)
    monkeypatch.setattr(ds, "DecisionRepository", FakeRepo)
    monkeypatch.setenv("SUPABASE_STORAGE_BUCKET", "custom-bucket")
    store = ds.create_decision_store("supabase", local_root=tmp_path)
    assert calls["bucket"] == "custom-bucket"
    assert calls["preflight"] is True
    assert calls["probe"] == "dc_" + "0" * 64
    assert store.backend == "supabase"
    assert not (tmp_path / "contexts").exists()


def test_create_decision_store_supabase_fail_closed(tmp_path, monkeypatch):
    import rules_ingestion.decision_storage as ds

    closed = {"storage": False, "repo": False}

    class FakeClient:
        def close(self):
            closed["storage"] = True

    class FakeStorage:
        def __init__(self, **kwargs):
            self.client = FakeClient()

        def preflight(self):
            raise RuntimeError("bucket not found")

    class FakeRepo:
        def __init__(self):
            pass

        def get_context(self, context_id):
            return None

        def close(self):
            closed["repo"] = True

    monkeypatch.setattr(ds, "SupabaseStorage", FakeStorage)
    monkeypatch.setattr(ds, "DecisionRepository", FakeRepo)
    with pytest.raises(RuntimeError):
        ds.create_decision_store("supabase", local_root=tmp_path)
    assert closed == {"storage": True, "repo": True}
    assert not (tmp_path / "contexts").exists()


def test_create_decision_store_supabase_repo_failure_closes_client(
        tmp_path, monkeypatch):
    import rules_ingestion.decision_storage as ds

    closed = {"storage": False}

    class FakeClient:
        def close(self):
            closed["storage"] = True

    class FakeStorage:
        def __init__(self, **kwargs):
            self.client = FakeClient()

        def preflight(self):
            raise AssertionError("preflight must not run")

    class FakeRepo:
        def __init__(self):
            raise ValueError("SUPABASE_DB_URL is required")

    monkeypatch.setattr(ds, "SupabaseStorage", FakeStorage)
    monkeypatch.setattr(ds, "DecisionRepository", FakeRepo)
    with pytest.raises(ValueError):
        ds.create_decision_store("supabase", local_root=tmp_path)
    assert closed["storage"] is True
    assert not (tmp_path / "contexts").exists()
