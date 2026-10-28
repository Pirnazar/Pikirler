"""Settings, background job status and health checks."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import DB, Account, ReadThoughts
from app.core.config import get_settings
from app.core.redis import get_redis
from app.modules.pikirler.models import Owner
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.system import JobOut, SettingsOut, SettingsUpdate
from app.modules.pikirler.services import jobs
from app.storage import get_storage

router = APIRouter(tags=["system"], responses=ERROR_RESPONSES)
health_router = APIRouter(prefix="/health", tags=["health"])


def _providers() -> dict[str, object]:
    s = get_settings()
    return {
        "embeddings": {
            "provider": s.embedding_provider,
            "model": s.embedding_model or None,
            "external": s.embedding_provider == "gemini"
            or (s.embedding_provider == "openai_compatible" and not s.embedding_is_local),
        },
        "llm": {
            "provider": s.llm_provider,
            "model": s.llm_model or None,
            "external": s.llm_provider == "gemini"
            or (s.llm_provider == "openai_compatible" and not s.llm_is_local),
        },
        "asr": {
            "provider": s.asr_provider,
            "model": s.asr_model or None,
            "external": s.asr_provider != "disabled" and not s.asr_is_local,
        },
    }


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(principal: Account, db: DB) -> SettingsOut:
    owner = await db.get(Owner, principal.owner_id)
    assert owner is not None
    return SettingsOut(
        display_name=owner.display_name,
        timezone=owner.timezone,
        ai_external_enabled=owner.ai_external_enabled,
        magic_word_enabled=owner.magic_word_hash is not None,
        providers=_providers(),
    )


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, principal: Account, db: DB) -> SettingsOut:
    owner = await db.get(Owner, principal.owner_id)
    assert owner is not None
    if body.display_name is not None:
        owner.display_name = body.display_name or None
    if body.timezone is not None:
        owner.timezone = body.timezone
    if body.ai_external_enabled is not None:
        owner.ai_external_enabled = body.ai_external_enabled
    await db.commit()
    return await get_settings_route(principal, db)


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    principal: ReadThoughts,
    db: DB,
    status: str | None = Query(default=None, pattern="^(pending|running|succeeded|failed|dead)$"),
    kind: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JobOut]:
    rows = await jobs.list_jobs(db, principal.owner_id, status, kind, limit)
    return [JobOut.model_validate(j) for j in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: uuid.UUID, principal: ReadThoughts, db: DB) -> JobOut:
    return JobOut.model_validate(await jobs.get_job(db, principal.owner_id, job_id))


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: uuid.UUID, principal: Account, db: DB) -> JobOut:
    return JobOut.model_validate(await jobs.retry_job(db, principal.owner_id, job_id))


@health_router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
async def ready(db: DB) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = type(exc).__name__
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = type(exc).__name__
    try:
        await get_storage().ping()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = type(exc).__name__
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "checks": checks},
        status_code=200 if healthy else 503,
    )
