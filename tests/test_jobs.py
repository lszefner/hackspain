from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import psycopg

from ingestion.storage import PostgresRepository, StorageRef


def _job(repository: PostgresRepository, *, work_key: str, max_attempts: int = 3):
    batch = repository.create_batch([], {"version": "jobs-test"})
    item = repository.register_input(batch["id"], f"{work_key}.pdf")
    job = repository.ensure_job(
        batch_id=batch["id"],
        input_id=item["id"],
        stage="interpretation",
        provider="deepseek",
        model="deepseek-test",
        work_key=work_key,
        max_attempts=max_attempts,
    )
    return batch, item, job


def test_atomic_parallel_claim_has_one_winner(db):
    _, _, job = _job(db, work_key="parallel-claim")

    def claim_once():
        return db.claim(job_id=job["id"], worker_id="parallel-test", lease_seconds=60)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: claim_once(), range(2)))

    winners = [value for value in claimed if value is not None]
    assert len(winners) == 1
    assert db.get_job(job["id"])["state"] == "running"


def test_expired_running_job_becomes_unknown_and_normal_claim_cannot_reclaim(
    db, postgres_dsn
):
    batch, _, job = _job(db, work_key="expired-no-reclaim")
    claimed = db.claim(job_id=job["id"], lease_seconds=60)
    assert claimed is not None
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute(
            "UPDATE ingestion.jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (job["id"],),
        )
        connection.commit()

    expired = db.reconcile_expired(batch["id"])
    assert [row["id"] for row in expired] == [job["id"]]
    assert db.get_job(job["id"])["state"] == "unknown"
    assert db.claim(job_id=job["id"]) is None


def test_saved_request_id_allows_explicit_recovery_only(db, postgres_dsn):
    batch, _, job = _job(db, work_key="recover-request")
    claimed = db.claim(job_id=job["id"], lease_seconds=60)
    attempt = db.begin_attempt(claimed["id"], claimed["lease_token"])
    db.record_request_id(attempt["id"], "provider-request-123")
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute(
            "UPDATE ingestion.jobs SET lease_expires_at = now() - interval '1 second' WHERE id = %s",
            (job["id"],),
        )
        connection.commit()
    db.reconcile_expired(batch["id"])

    assert db.claim(job_id=job["id"]) is None
    recovery = db.claim_recovery(job["id"], worker_id="recovery-test", lease_seconds=60)
    assert recovery is not None
    assert recovery["state"] == "running"
    assert recovery["attempt_count"] == 1
    assert (
        db.list_attempts(job["id"])[0]["provider_request_id"] == "provider-request-123"
    )


def test_retry_creates_one_fresh_generation_and_ensure_selects_it(db):
    batch, item, job = _job(db, work_key="retry-generation")
    claimed = db.claim(job_id=job["id"])
    db.begin_attempt(claimed["id"], claimed["lease_token"])
    db.fail(claimed["id"], claimed["lease_token"], "invalid response", retryable=False)

    retries = db.retry_jobs(batch["id"], "interpretation")
    assert len(retries) == 1
    assert retries[0]["id"] != job["id"]
    assert retries[0]["state"] == "pending"
    resolved = db.ensure_job(
        batch_id=batch["id"],
        input_id=item["id"],
        stage="interpretation",
        provider="deepseek",
        model="deepseek-test",
        work_key="retry-generation",
    )
    assert resolved["id"] == retries[0]["id"]
    assert db.retry_jobs(batch["id"], "interpretation") == []


def test_verified_artifact_can_be_reused_before_completion(db):
    _, _, job = _job(db, work_key="artifact-before-completion")
    claimed = db.claim(job_id=job["id"])
    db.begin_attempt(claimed["id"], claimed["lease_token"])
    reference = StorageRef(
        "a" * 64, "sha256/aa/orphan/invoice.json", 18, "application/json", "invoice"
    )

    first = db.save_artifact(reference, payload={"status": "completed"})
    # A worker crash after upload but before completion can safely publish the
    # same immutable object and metadata on its next pass.
    reused = db.save_artifact(reference, payload={"status": "completed"})
    assert reused["id"] == first["id"]
    completed = db.complete(job["id"], claimed["lease_token"], reused["id"])
    assert completed["state"] == "succeeded"
    assert completed["artifact_id"] == reused["id"]


def test_max_attempts_bounds_transport_retries(db):
    _, _, job = _job(db, work_key="max-attempts", max_attempts=2)
    first = db.claim(job_id=job["id"])
    db.begin_attempt(first["id"], first["lease_token"])
    retry_wait = db.fail(first["id"], first["lease_token"], "timeout", retryable=True)
    assert retry_wait["state"] == "retry_wait"

    second = db.claim(job_id=job["id"])
    db.begin_attempt(second["id"], second["lease_token"])
    exhausted = db.fail(second["id"], second["lease_token"], "timeout", retryable=True)
    assert exhausted["state"] == "failed"
    assert exhausted["attempt_count"] == 2
    assert db.claim(job_id=job["id"]) is None
