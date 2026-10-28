"""Export, import and complete deletion of the owner's data."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.deps import DB, Account
from app.core.database import utcnow
from app.core.exceptions import Forbidden
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.services import auth as auth_service
from app.modules.pikirler.services import data_export as service
from app.storage import get_storage

router = APIRouter(tags=["data"], responses=ERROR_RESPONSES)


class JobAccepted(Schema):
    job_id: uuid.UUID
    status_url: str


class PurgeRequest(Schema):
    password: str = Field(min_length=1, max_length=1024)
    confirm: str = Field(description=f'Must be exactly "{service.PURGE_PHRASE}"')


def _download_headers(name: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}


@router.get(
    "/export/json", response_class=Response, responses={200: {"content": {"application/json": {}}}}
)
async def export_json(principal: Account, db: DB) -> Response:
    """Everything except file bytes: thoughts with revisions, threads, tags,
    projects, relations, attachment metadata and transcripts."""
    doc = await service.build_export(db, principal.owner_id)
    return Response(
        service.to_json_bytes(doc),
        media_type="application/json",
        headers=_download_headers(f"pikirler-{utcnow():%Y%m%d}.json"),
    )


@router.get(
    "/export/markdown",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def export_markdown(principal: Account, db: DB) -> Response:
    doc = await service.build_export(db, principal.owner_id)
    return Response(
        service.markdown_bytes(doc),
        media_type="application/zip",
        headers=_download_headers(f"pikirler-markdown-{utcnow():%Y%m%d}.zip"),
    )


@router.post("/export/archive", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def export_archive(principal: Account, db: DB) -> JobAccepted:
    """Full backup (data.json + Markdown + original attachments), built in the background."""
    job_id = await service.request_export(db, principal.owner_id)
    return JobAccepted(job_id=job_id, status_url=f"/jobs/{job_id}")


@router.get(
    "/export/archive/{job_id}",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def download_archive(job_id: uuid.UUID, principal: Account, db: DB) -> Response:
    key, size = await service.export_archive_location(db, principal.owner_id, job_id)
    return StreamingResponse(
        get_storage().open_stream(key),
        media_type="application/zip",
        headers={
            **_download_headers(f"pikirler-archive-{job_id}.zip"),
            "Content-Length": str(size),
        },
    )


@router.post("/import", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def import_backup(
    request: Request, principal: Account, db: DB, filename: str = "import.zip"
) -> JobAccepted:
    """Upload a ``data.json`` or an archive ZIP as the raw request body.
    Existing ids are skipped, so importing twice is harmless."""
    job_id = await service.request_import(db, principal.owner_id, filename, request.stream())
    return JobAccepted(job_id=job_id, status_url=f"/jobs/{job_id}")


@router.post("/account/purge")
async def purge(body: PurgeRequest, principal: Account, db: DB) -> dict[str, int]:
    if body.confirm != service.PURGE_PHRASE:
        raise Forbidden("Confirmation phrase does not match", code="purge_not_confirmed")
    await auth_service.verify_password(db, principal.owner_id, body.password)
    return await service.purge_all(db, principal.owner_id)
