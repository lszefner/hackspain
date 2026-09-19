from __future__ import annotations

import hashlib

from ingestion.storage import SupabaseStorage, canonical_json_bytes, sha256_bytes


def test_canonical_json_hash_is_independent_of_mapping_order() -> None:
    left = canonical_json_bytes({"b": 2, "a": [1, "ñ"]})
    right = canonical_json_bytes({"a": [1, "ñ"], "b": 2})
    assert left == right
    assert sha256_bytes(left) == hashlib.sha256(right).hexdigest()


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

    def post(self, url: str, *, content: bytes, headers: dict[str, str]) -> _Response:
        assert headers["apikey"] == "sb_secret_test"
        assert "Authorization" not in headers
        self.posts.append((url, content))
        self.objects.setdefault(url, content)
        return _Response(status_code=200)

    def get(self, url: str, *, headers: dict[str, str]) -> _Response:
        assert headers["apikey"] == "sb_secret_test"
        assert "Authorization" not in headers
        return _Response(self.objects[url])


def test_supabase_storage_is_content_addressed_and_retries_are_immutable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    client = _Client()
    storage = SupabaseStorage(url="https://example.test", client=client)
    data = b"invoice bytes"

    first = storage.put(data, "original", "application/pdf")
    second = storage.put(data, "original", "application/pdf")

    assert first == second
    assert first.sha256 == hashlib.sha256(data).hexdigest()
    assert first.object_key.startswith(f"sha256/{first.sha256[:2]}/{first.sha256}/")
    assert storage.get(first.object_key) == data
    assert len(client.objects) == 1


def test_verified_duplicate_upload_uses_bounded_receipt_cache(monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    client = _Client()
    storage = SupabaseStorage(url="https://example.test", client=client)
    for _ in range(20):
        storage.put(b'{"status":"IN_QUEUE"}', "provider-response", "application/json")
    assert len(client.posts) == 1
    for index in range(257):
        storage.put(str(index).encode(), "unique")
    assert len(storage._verified) == 256


def test_repository_pool_reuses_connection_and_rolls_back_failed_operation(db):
    pids = [
        db._run(
            lambda conn: conn.execute("SELECT pg_backend_pid() AS pid").fetchone()[
                "pid"
            ]
        )
        for _ in range(10)
    ]
    assert len(set(pids)) <= 4
    assert len(set(pids)) < len(pids)
    import pytest

    with pytest.raises(RuntimeError, match="synthetic"):

        def fail(conn):
            conn.execute("SELECT 1")
            raise RuntimeError("synthetic")

        db._run(fail)
    assert (
        db._run(lambda conn: conn.execute("SELECT 42 AS value").fetchone()["value"])
        == 42
    )
    db.close()
