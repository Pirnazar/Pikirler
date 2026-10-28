"""ARQ worker: ``arq app.worker.WorkerSettings``.

Runs outbox jobs and the periodic dispatcher/cleanup. Heavy work (ASR,
thumbnails, embeddings) never runs inside the API process.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.database import get_database
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.modules.pikirler.services import jobs, maintenance
from app.modules.pikirler.tasks import runner
from app.modules.pikirler.tasks.handlers import ensure_registered

log = logging.getLogger("pikirler.worker")


async def run_job(ctx: dict[str, Any], job_id: str) -> str:
    return await runner.execute(uuid.UUID(job_id))


async def dispatch(ctx: dict[str, Any]) -> int:
    async with get_database().sessionmaker() as db:
        return await jobs.dispatch_due(db)


async def cleanup(ctx: dict[str, Any]) -> dict[str, Any]:
    async with get_database().sessionmaker() as db:
        result: dict[str, Any] = {}
        result.update(await maintenance.cleanup_media(db))
        result.update(await maintenance.purge_deleted_thoughts(db))
        result.update(await maintenance.prune_bookkeeping(db))
    log.info("cleanup_done", extra={"count": sum(v for v in result.values() if isinstance(v, int))})
    return result


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging()
    ensure_registered()
    jobs.set_pool(ctx["redis"])


async def shutdown(ctx: dict[str, Any]) -> None:
    jobs.set_pool(None)
    await close_redis()
    await get_database().dispose()


_settings = get_settings()


class WorkerSettings:
    functions = [run_job]
    cron_jobs = [
        cron(
            dispatch,
            second=set(range(0, 60, max(1, _settings.job_dispatch_interval_seconds))),
            run_at_startup=True,
            unique=True,
        ),
        cron(cleanup, minute={17}, unique=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = 4
    # Long enough for a long voice note on a slow ASR box.
    job_timeout = int(_settings.asr_timeout_seconds) + 120
    keep_result = 3600
    max_tries = 1  # retries are ours (jobs table), not ARQ's
