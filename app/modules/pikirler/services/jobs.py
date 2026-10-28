"""Transactional outbox for background work.

``enqueue`` only inserts a ``jobs`` row inside the caller's transaction, so a
job exists if and only if the data it refers to was committed. After commit the
job id is pushed to ARQ (``kick``); if Redis is down or the process dies in
between, the worker's dispatcher cron finds the row and enqueues it later.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from sqlalchemy import or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound
from app.modules.pikirler.models import Job
from app.modules.pikirler.models.enums import JobStatus

log = logging.getLogger("pikirler.jobs")

PENDING_KEY = "pikirler_pending_jobs"
ARQ_FUNCTION = "run_job"

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()  # type: ignore[attr-defined]
        _pool = None


def set_pool(pool: ArqRedis | None) -> None:
    global _pool
    _pool = pool


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any],
    *,
    owner_id: uuid.UUID | None,
    dedupe_key: str | None = None,
    max_attempts: int | None = None,
) -> uuid.UUID:
    """Insert a job row (no commit). With ``dedupe_key`` a still-pending or
    running job with the same key is reused instead of creating a second one."""
    job_id = uuid.uuid4()
    stmt = insert(Job).values(
        id=job_id,
        owner_id=owner_id,
        kind=kind,
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts or get_settings().job_max_attempts,
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["dedupe_key"],
            # Literal predicate: PostgreSQL must match it to the partial unique index.
            index_where=text("dedupe_key IS NOT NULL AND status IN ('pending', 'running')"),
        )
    result = await db.execute(stmt.returning(Job.id))
    inserted = result.scalar_one_or_none()
    if inserted is None:
        existing = (
            await db.execute(
                select(Job.id).where(
                    Job.dedupe_key == dedupe_key, Job.status.in_(["pending", "running"])
                )
            )
        ).scalar_one()
        return existing
    db.info.setdefault(PENDING_KEY, []).append(inserted)
    return inserted


def arq_job_id(job_id: uuid.UUID, attempts: int) -> str:
    # A new ARQ id per attempt: ARQ refuses an id whose previous result it still holds.
    return f"pikirler:{job_id}:{attempts}"


async def push(job_ids: list[uuid.UUID], attempts: dict[uuid.UUID, int] | None = None) -> None:
    pool = await get_pool()
    for job_id in job_ids:
        await pool.enqueue_job(
            ARQ_FUNCTION, str(job_id), _job_id=arq_job_id(job_id, (attempts or {}).get(job_id, 0))
        )


async def kick(db: AsyncSession) -> None:
    """Push jobs created in this (committed) session. Failures are harmless."""
    job_ids: list[uuid.UUID] = db.info.pop(PENDING_KEY, [])
    if not job_ids:
        return
    try:
        await push(job_ids)
        await db.execute(update(Job).where(Job.id.in_(job_ids)).values(enqueued_at=utcnow()))
        await db.commit()
    except Exception as exc:  # the dispatcher retries
        log.warning(
            "job_enqueue_deferred", extra={"error_type": type(exc).__name__, "count": len(job_ids)}
        )


async def dispatch_due(db: AsyncSession, limit: int = 200) -> int:
    """Enqueue jobs that are due but not in the queue: never pushed, waiting
    for a retry, or stuck as running past their lease."""
    now = utcnow()
    requeue_after = now - timedelta(minutes=2)
    rows = (
        await db.execute(
            select(Job.id, Job.attempts)
            .where(
                or_(
                    (Job.status == JobStatus.pending.value)
                    & (Job.run_after <= now)
                    & (Job.enqueued_at.is_(None) | (Job.enqueued_at < requeue_after)),
                    (Job.status == JobStatus.failed.value)
                    & (Job.run_after <= now)
                    & (Job.enqueued_at.is_(None) | (Job.enqueued_at < Job.run_after)),
                    (Job.status == JobStatus.running.value) & (Job.locked_until < now),
                )
            )
            .order_by(Job.run_after)
            .limit(limit)
        )
    ).all()
    if not rows:
        return 0
    await push([r.id for r in rows], {r.id: r.attempts for r in rows})
    await db.execute(update(Job).where(Job.id.in_([r.id for r in rows])).values(enqueued_at=now))
    await db.commit()
    return len(rows)


async def get_job(db: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    job = (
        await db.execute(select(Job).where(Job.id == job_id, Job.owner_id == owner_id))
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("Job not found", code="job_not_found")
    return job


async def list_jobs(
    db: AsyncSession, owner_id: uuid.UUID, status: str | None, kind: str | None, limit: int
) -> list[Job]:
    stmt = select(Job).where(Job.owner_id == owner_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if kind:
        stmt = stmt.where(Job.kind == kind)
    return list((await db.execute(stmt.order_by(Job.created_at.desc()).limit(limit))).scalars())


async def retry_job(db: AsyncSession, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    job = await get_job(db, owner_id, job_id)
    if job.status not in (JobStatus.failed.value, JobStatus.dead.value):
        raise Conflict("Only failed jobs can be retried", code="job_not_retryable")
    job.status = JobStatus.failed.value
    job.run_after = utcnow()
    job.enqueued_at = None
    job.max_attempts = max(job.max_attempts, job.attempts + 1)
    await db.commit()
    try:
        await push([job.id], {job.id: job.attempts})
    except Exception as exc:
        log.warning("job_enqueue_deferred", extra={"error_type": type(exc).__name__})
    return job
