"""Executes outbox jobs. ARQ only delivers job ids; state lives in ``jobs``.

* claim: ``SELECT ... FOR UPDATE SKIP LOCKED`` so a job runs once even if it
  was enqueued twice (dispatcher + request) or two workers race.
* lease: ``locked_until``; a worker that dies leaves the job to be re-run.
* retry: exponential backoff until ``max_attempts``, then ``dead``.
* handlers are idempotent, so a re-run after a crash is harmless.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_database, utcnow
from app.core.exceptions import AppError
from app.modules.pikirler.integrations.http import ProviderError
from app.modules.pikirler.models import Job
from app.modules.pikirler.models.enums import JobStatus

log = logging.getLogger("pikirler.worker")

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any] | None]]
GiveUp = Callable[[AsyncSession, dict[str, Any], str], Awaitable[None]]


@dataclass(frozen=True)
class JobType:
    run: Handler
    on_dead: GiveUp | None = None


REGISTRY: dict[str, JobType] = {}


def register(kind: str, run: Handler, on_dead: GiveUp | None = None) -> None:
    REGISTRY[kind] = JobType(run, on_dead)


class PermanentJobError(Exception):
    """Retrying cannot help (bad input, provider rejected the request)."""


def backoff(attempt: int) -> timedelta:
    return timedelta(seconds=min(3600, 15 * 2 ** (attempt - 1)))


def _describe(exc: BaseException) -> str:
    # AppError messages are written by us and contain no private text.
    if isinstance(exc, AppError | ProviderError | PermanentJobError):
        return f"{type(exc).__name__}: {exc}"[:2000]
    return type(exc).__name__


async def _claim(db: AsyncSession, job_id: uuid.UUID) -> Job | None:
    now = utcnow()
    job = (
        await db.execute(
            select(Job)
            .where(
                Job.id == job_id,
                or_(
                    Job.status.in_([JobStatus.pending.value, JobStatus.failed.value])
                    & (Job.run_after <= now),
                    (Job.status == JobStatus.running.value) & (Job.locked_until < now),
                ),
            )
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        return None
    job.status = JobStatus.running.value
    job.attempts += 1
    job.started_at = now
    job.locked_until = now + timedelta(seconds=get_settings().job_stale_after_seconds)
    await db.commit()
    return job


async def execute(job_id: uuid.UUID) -> str:
    """Run one job attempt. Returns the resulting status (or 'skipped')."""
    db_factory = get_database().sessionmaker
    async with db_factory() as db:
        job = await _claim(db, job_id)
        if job is None:
            return "skipped"
        kind, attempt, max_attempts = job.kind, job.attempts, job.max_attempts
        payload = {**job.payload, "_job_id": str(job.id)}

    job_type = REGISTRY.get(kind)
    extra = {"job_id": str(job_id), "job_kind": kind, "attempt": attempt}
    result: dict[str, Any] | None = None
    error: BaseException | None = None
    try:
        if job_type is None:
            raise PermanentJobError(f"unknown job kind {kind}")
        async with db_factory() as db:
            result = await job_type.run(db, payload)
    except (PermanentJobError, ProviderError) as exc:
        error, attempt = exc, max_attempts  # no point retrying
    except Exception as exc:
        error = exc

    async with db_factory() as db:
        job = await db.get(Job, job_id, with_for_update=True)
        assert job is not None
        now = utcnow()
        job.locked_until = None
        if error is None:
            job.status = JobStatus.succeeded.value
            job.result = result or {}
            job.last_error = None
            job.finished_at = now
            log.info("job_succeeded", extra=extra)
        else:
            job.last_error = _describe(error)
            if attempt >= max_attempts:
                job.status = JobStatus.dead.value
                job.finished_at = now
                log.warning("job_dead", extra={**extra, "error_type": type(error).__name__})
            else:
                job.status = JobStatus.failed.value
                job.run_after = now + backoff(attempt)
                job.enqueued_at = None
                log.info("job_retry_scheduled", extra={**extra, "error_type": type(error).__name__})
        await db.commit()
        status = job.status
        if status == JobStatus.dead.value and job_type and job_type.on_dead and error:
            async with db_factory() as cleanup_db:
                await job_type.on_dead(cleanup_db, payload, _describe(error))
    return status
