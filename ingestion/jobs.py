"""Bounded job lifecycle helpers.

This module contains orchestration policy only; provider adapters remain
responsible for making network calls.  Callers must claim and begin an attempt
before submitting any request, and must use the returned lease token when
publishing its result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .storage import PostgresRepository, StorageRef


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: int = 5
    max_delay_seconds: int = 300

    def delay(self, attempt_number: int) -> int:
        if attempt_number < 1:
            return self.base_delay_seconds
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** (attempt_number - 1)),
        )


class JobCoordinator:
    """Convenient repository-facing lifecycle used by the ingestion worker."""

    def __init__(
        self,
        repository: PostgresRepository,
        *,
        worker_id: str | None = None,
        lease_seconds: int = 300,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.repository = repository
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.retry_policy = retry_policy or RetryPolicy()

    def ensure_job(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("max_attempts", self.retry_policy.max_attempts)
        return self.repository.ensure_job(**kwargs)

    def claim(
        self, *, job_id: str | None = None, stage: str | None = None
    ) -> dict[str, Any] | None:
        return self.repository.claim(
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
            job_id=job_id,
            stage=stage,
        )

    def begin_attempt(
        self, job: Mapping[str, Any], *, request_id: str | None = None
    ) -> dict[str, Any]:
        token = job.get("lease_token")
        if not token:
            raise ValueError("claimed job has no lease token")
        return self.repository.begin_attempt(job["id"], token, request_id=request_id)

    def record_request_id(
        self, attempt: Mapping[str, Any], request_id: str
    ) -> dict[str, Any]:
        return self.repository.record_request_id(attempt["id"], request_id)

    def publish(
        self,
        job: Mapping[str, Any],
        ref: StorageRef,
        *,
        payload: Any | None = None,
        interpreter: str | None = None,
        status: str = "succeeded",
        result_status: str | None = None,
        parent_artifact_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Persist a verified artifact, then atomically complete its job."""

        artifact = self.repository.save_artifact(
            ref, payload=payload, parent_artifact_ids=parent_artifact_ids
        )
        token = job.get("lease_token")
        if not token:
            raise ValueError("claimed job has no lease token")
        return self.repository.complete(
            job["id"],
            token,
            artifact["id"],
            status=status,
            result_status=result_status,
            interpreter=interpreter,
        )

    def fail(
        self,
        job: Mapping[str, Any],
        error: Mapping[str, Any] | str,
        *,
        retryable: bool = True,
        unknown: bool = False,
        attempt_number: int | None = None,
    ) -> dict[str, Any]:
        token = job.get("lease_token")
        if not token:
            raise ValueError("claimed job has no lease token")
        attempt = attempt_number or int(job.get("attempt_count") or 1)
        delay = self.retry_policy.delay(attempt) if retryable else 0
        return self.repository.fail(
            job["id"],
            token,
            error,
            retryable=retryable,
            unknown=unknown,
            retry_after_seconds=delay,
        )


__all__ = ["JobCoordinator", "RetryPolicy"]
