from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field

from app.api.deps import DB, MediaRead, MediaWrite
from app.core.config import get_settings
from app.core.exceptions import AppError, NotFound
from app.modules.pikirler.models import Attachment
from app.modules.pikirler.models.enums import AttachmentKind, ProcessingStatus
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.schemas.thoughts import AttachmentRef
from app.modules.pikirler.services import media as service
from app.modules.pikirler.services.media_processing import PREVIEW_MAX, sanitize_image
from app.modules.pikirler.services.presenter import attachment_ref
from app.storage import get_storage

router = APIRouter(prefix="/media", tags=["media"], responses=ERROR_RESPONSES)

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class UploadCreate(Schema):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=127)
    size: int = Field(gt=0)
    checksum_sha256: str = Field(min_length=64, max_length=64)


class UploadOut(Schema):
    id: uuid.UUID
    status: str
    upload_url: str = Field(description="PUT the raw bytes here (owner session + CSRF).")
    direct_upload_url: str | None = Field(
        default=None, description="Optional short-lived presigned S3 PUT URL"
    )
    expires_at: datetime
    attachment: AttachmentRef


class MediaOut(AttachmentRef):
    download_url: str | None = Field(
        default=None, description="Short-lived presigned URL when enabled"
    )
    checksum: str
    created_at: datetime


@router.post("/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def create_upload(body: UploadCreate, principal: MediaWrite, db: DB) -> UploadOut:
    ticket = await service.create_upload(
        db,
        principal.owner_id,
        body.filename,
        body.mime_type,
        body.size,
        body.checksum_sha256.lower(),
    )
    att = ticket.attachment
    return UploadOut(
        id=att.id,
        status=att.processing_status,
        upload_url=ticket.upload_url,
        direct_upload_url=ticket.direct_upload_url,
        expires_at=ticket.expires_at,
        attachment=attachment_ref(att),
    )


@router.put("/uploads/{attachment_id}/content", response_model=AttachmentRef)
async def upload_content(
    attachment_id: uuid.UUID, request: Request, principal: MediaWrite, db: DB
) -> AttachmentRef:
    att = await service.receive_content(db, principal.owner_id, attachment_id, request.stream())
    return attachment_ref(att)


@router.post("/uploads/{attachment_id}/complete", response_model=AttachmentRef)
async def complete_upload(attachment_id: uuid.UUID, principal: MediaWrite, db: DB) -> AttachmentRef:
    att = await service.complete_upload(db, principal.owner_id, attachment_id)
    return attachment_ref(att)


@router.get("/{attachment_id}", response_model=MediaOut)
async def get_media(attachment_id: uuid.UUID, principal: MediaRead, db: DB) -> MediaOut:
    att = await service.get_attachment(db, principal.owner_id, attachment_id)
    ref = attachment_ref(att)
    download_url = None
    if att.processing_status == ProcessingStatus.ready.value and att.kind != "image":
        download_url = await get_storage().presigned_get(
            att.storage_key, att.safe_filename, att.mime_type, get_settings().signed_url_ttl_seconds
        )
    return MediaOut(
        **ref.model_dump(),
        download_url=download_url,
        checksum=att.checksum,
        created_at=att.created_at,
    )


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_media(attachment_id: uuid.UUID, principal: MediaWrite, db: DB) -> Response:
    await service.delete_attachment(db, principal.owner_id, attachment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _private_headers(filename: str, inline: bool) -> dict[str, str]:
    return {
        "Content-Disposition": service.content_disposition(filename, inline),
        "Cache-Control": "private, max-age=300",
        # A stored file is never interpreted as a page of this origin.
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
    }


async def _stream(
    key: str, size: int, mime: str, filename: str, inline: bool, range_header: str | None
) -> Response:
    headers = _private_headers(filename, inline)
    storage = get_storage()
    match = _RANGE.match(range_header or "")
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            start, end = max(0, size - int(match.group(2))), size - 1
        end = min(end, size - 1)
        if start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers.update(
            {"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)}
        )
        return StreamingResponse(
            storage.open_stream(key, start=start, end=end),
            status_code=206,
            media_type=mime,
            headers=headers,
        )
    headers["Content-Length"] = str(size)
    return StreamingResponse(storage.open_stream(key), media_type=mime, headers=headers)


@router.get(
    "/{attachment_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def media_content(
    attachment_id: uuid.UUID,
    request: Request,
    principal: MediaRead,
    db: DB,
    original: bool = Query(
        default=False,
        description="Images: the untouched original incl. EXIF/GPS. "
        "Default serves the metadata-free preview.",
    ),
) -> Response:
    att = await service.get_attachment(db, principal.owner_id, attachment_id)
    if att.processing_status in (
        ProcessingStatus.pending_upload.value,
        ProcessingStatus.failed.value,
    ):
        raise NotFound("Content not available", code="media_not_ready")
    inline = att.kind in ("image", "video", "audio", "document") and att.mime_type != "text/html"
    if att.kind == AttachmentKind.image.value and not original:
        return await _image_preview(att)
    return await _stream(
        att.storage_key,
        att.file_size,
        att.mime_type,
        att.safe_filename,
        inline,
        request.headers.get("range"),
    )


async def _image_preview(att: Attachment) -> Response:
    storage = get_storage()
    if att.preview_key:
        info = await storage.head(att.preview_key)
        if info is not None:
            mime = (att.media_info or {}).get("preview_mime", "image/jpeg")
            return await _stream(att.preview_key, info.size, mime, att.safe_filename, True, None)
    # Not processed yet: sanitise on the fly rather than leak EXIF.
    data = b"".join([chunk async for chunk in storage.open_stream(att.storage_key)])
    try:
        clean, mime, _, _ = await asyncio.to_thread(sanitize_image, data, PREVIEW_MAX)
    except Exception as exc:
        raise AppError(
            "No metadata-free preview exists for this image format; "
            "use ?original=true to download the original",
            code="preview_unavailable",
        ) from exc
    return Response(clean, media_type=mime, headers=_private_headers(att.safe_filename, True))


@router.get(
    "/{attachment_id}/thumbnail",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def media_thumbnail(attachment_id: uuid.UUID, principal: MediaRead, db: DB) -> Response:
    att = await service.get_attachment(db, principal.owner_id, attachment_id)
    if not att.thumbnail_key:
        raise NotFound("No thumbnail", code="thumbnail_not_found")
    info = await get_storage().head(att.thumbnail_key)
    if info is None:
        raise NotFound("No thumbnail", code="thumbnail_not_found")
    mime = "image/png" if att.thumbnail_key.endswith(".png") else "image/jpeg"
    return await _stream(att.thumbnail_key, info.size, mime, att.safe_filename, True, None)
