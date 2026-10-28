"""Private media: upload sessions, verification, sanitised delivery.

Upload flow (retry-safe at every step)::

    POST /media/uploads                 declare name, type, size, sha256 -> upload id
    PUT  /media/uploads/{id}/content    send the bytes (repeatable; overwrites)
    POST /media/uploads/{id}/complete   server re-reads the object: size, sha256 and
                                        the real file type (magic bytes) must match

Only then can the attachment be linked to a thought. A client can therefore not
"complete" an upload it never sent, nor smuggle a different file type in.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath

import filetype
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound, PayloadTooLarge, ValidationFailed
from app.modules.pikirler.models import Attachment, ThoughtAttachment
from app.modules.pikirler.models.enums import AttachmentKind, ProcessingStatus
from app.modules.pikirler.services import jobs
from app.storage import get_storage
from app.storage.base import TooLarge

PROCESS_JOB = "process_attachment"

ALLOWED: dict[str, AttachmentKind] = {
    "image/jpeg": AttachmentKind.image,
    "image/png": AttachmentKind.image,
    "image/webp": AttachmentKind.image,
    "image/gif": AttachmentKind.image,
    "image/heic": AttachmentKind.image,
    "image/heif": AttachmentKind.image,
    "video/mp4": AttachmentKind.video,
    "video/quicktime": AttachmentKind.video,
    "video/webm": AttachmentKind.video,
    "video/x-matroska": AttachmentKind.video,
    "audio/mpeg": AttachmentKind.audio,
    "audio/mp4": AttachmentKind.audio,
    "audio/x-m4a": AttachmentKind.audio,
    "audio/ogg": AttachmentKind.audio,
    "audio/webm": AttachmentKind.audio,
    "audio/wav": AttachmentKind.audio,
    "audio/x-wav": AttachmentKind.audio,
    "audio/flac": AttachmentKind.audio,
    "audio/aac": AttachmentKind.audio,
    "audio/amr": AttachmentKind.audio,
    "application/pdf": AttachmentKind.document,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        AttachmentKind.document
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": AttachmentKind.document,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        AttachmentKind.document
    ),
    "application/vnd.oasis.opendocument.text": AttachmentKind.document,
    "text/plain": AttachmentKind.document,
    "text/markdown": AttachmentKind.document,
    "text/csv": AttachmentKind.document,
}
# Same container, different declared kind: an .m4a sniffs as MP4, voice notes
# from browsers are WebM/Ogg containers, etc.
_EQUIVALENT = {
    "audio/mp4": {"video/mp4", "audio/mp4", "audio/x-m4a"},
    "audio/x-m4a": {"video/mp4", "audio/mp4", "audio/x-m4a"},
    "audio/webm": {"video/webm", "audio/webm", "video/x-matroska"},
    "video/webm": {"video/webm", "video/x-matroska"},
    "video/x-matroska": {"video/webm", "video/x-matroska"},
    "audio/wav": {"audio/x-wav", "audio/wav"},
    "audio/x-wav": {"audio/x-wav", "audio/wav"},
    "audio/ogg": {"audio/ogg", "video/ogg", "application/ogg", "audio/opus"},
    "image/heif": {"image/heic", "image/heif"},
    "image/heic": {"image/heic", "image/heif"},
    "audio/aac": {"audio/aac", "audio/x-aac"},
}
_OOXML = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word/",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xl/",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "ppt/",
}
_TEXT_TYPES = {"text/plain", "text/markdown", "text/csv"}
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_SNIFF_BYTES = 64 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def max_bytes_for(kind: AttachmentKind) -> int:
    s = get_settings()
    return {
        AttachmentKind.image: s.max_image_bytes,
        AttachmentKind.video: s.max_video_bytes,
        AttachmentKind.audio: s.max_audio_bytes,
        AttachmentKind.document: s.max_document_bytes,
    }.get(kind, s.max_document_bytes)


def safe_filename(name: str) -> str:
    """Display-safe basename: no directories, control characters or reserved names."""
    name = unicodedata.normalize("NFKC", name).replace("\\", "/")
    name = PurePosixPath(name).name
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = "file"
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) <= 16:
        return stem[: 200 - len(ext)] + "." + ext
    return name[:200]


def storage_key(owner_id: uuid.UUID, attachment_id: uuid.UUID, variant: str) -> str:
    return f"owners/{owner_id}/attachments/{attachment_id}/{variant}"


@dataclass(frozen=True)
class UploadTicket:
    attachment: Attachment
    upload_url: str
    direct_upload_url: str | None
    expires_at: datetime


async def create_upload(
    db: AsyncSession, owner_id: uuid.UUID, filename: str, mime_type: str, size: int, checksum: str
) -> UploadTicket:
    mime = mime_type.split(";")[0].strip().lower()
    kind = ALLOWED.get(mime)
    if kind is None:
        raise ValidationFailed(
            "This file type is not allowed",
            code="unsupported_media_type",
            details={"mime_type": mime},
        )
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValidationFailed("checksum must be a lowercase hex SHA-256", code="invalid_checksum")
    limit = max_bytes_for(kind)
    if size <= 0 or size > limit:
        raise PayloadTooLarge(
            f"Files of this type may be up to {limit} bytes", details={"max_bytes": limit}
        )

    settings = get_settings()
    # Retrying the same declaration returns the same upload instead of a duplicate.
    existing = (
        await db.execute(
            select(Attachment)
            .where(
                Attachment.owner_id == owner_id,
                Attachment.checksum == checksum,
                Attachment.file_size == size,
                Attachment.deleted_at.is_(None),
                Attachment.processing_status != ProcessingStatus.failed.value,
            )
            .order_by(Attachment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    attachment = existing
    if attachment is None:
        attachment_id = uuid.uuid4()
        attachment = Attachment(
            id=attachment_id,
            owner_id=owner_id,
            storage_key=storage_key(owner_id, attachment_id, "original"),
            original_filename=_CONTROL.sub("", filename)[:255] or "file",
            safe_filename=safe_filename(filename),
            mime_type=mime,
            declared_mime_type=mime,
            kind=kind.value,
            file_size=size,
            checksum=checksum,
            processing_status=ProcessingStatus.pending_upload.value,
        )
        db.add(attachment)
        await db.commit()
    expires = attachment.created_at + timedelta(hours=settings.upload_session_ttl_hours)
    direct = None
    if attachment.processing_status == ProcessingStatus.pending_upload.value:
        direct = await get_storage().presigned_put(
            attachment.storage_key, attachment.mime_type, settings.signed_url_ttl_seconds
        )
    return UploadTicket(
        attachment=attachment,
        upload_url=f"{settings.api_prefix}/media/uploads/{attachment.id}/content",
        direct_upload_url=direct,
        expires_at=expires,
    )


async def get_attachment(
    db: AsyncSession, owner_id: uuid.UUID, attachment_id: uuid.UUID, include_deleted: bool = False
) -> Attachment:
    stmt = select(Attachment).where(Attachment.id == attachment_id, Attachment.owner_id == owner_id)
    if not include_deleted:
        stmt = stmt.where(Attachment.deleted_at.is_(None))
    attachment = (await db.execute(stmt)).scalar_one_or_none()
    if attachment is None:
        raise NotFound("Attachment not found", code="attachment_not_found")
    return attachment


async def receive_content(
    db: AsyncSession, owner_id: uuid.UUID, attachment_id: uuid.UUID, stream: AsyncIterator[bytes]
) -> Attachment:
    attachment = await get_attachment(db, owner_id, attachment_id)
    if attachment.processing_status != ProcessingStatus.pending_upload.value:
        raise Conflict("Upload already completed", code="upload_completed")
    try:
        await get_storage().put_stream(
            attachment.storage_key, stream, attachment.mime_type, max_bytes=attachment.file_size
        )
    except TooLarge as exc:
        raise PayloadTooLarge(
            "More bytes than declared", details={"declared_size": attachment.file_size}
        ) from exc
    return attachment


def _sniff(prefix: bytes, declared: str) -> str | None:
    """Return the actual MIME type, or None if it does not match the declaration."""
    if declared in _TEXT_TYPES:
        if b"\x00" in prefix:
            return None
        try:
            prefix.decode("utf-8")
        except UnicodeDecodeError as exc:
            # A multi-byte character may be cut at the sniff boundary.
            if exc.start < len(prefix) - 4:
                return None
        return declared
    guess = filetype.guess(prefix)
    actual = guess.mime if guess else None
    if actual is None:
        return None
    if actual == declared or actual in _EQUIVALENT.get(declared, set()):
        return declared
    if declared in _OOXML and actual in {"application/zip", *_OOXML}:
        return declared
    return None


def _verify_ooxml(data: bytes, declared: str) -> bool:
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return False
    return "[Content_Types].xml" in names and any(n.startswith(_OOXML[declared]) for n in names)


async def complete_upload(
    db: AsyncSession, owner_id: uuid.UUID, attachment_id: uuid.UUID
) -> Attachment:
    attachment = await get_attachment(db, owner_id, attachment_id)
    if attachment.processing_status != ProcessingStatus.pending_upload.value:
        if attachment.processing_status == ProcessingStatus.failed.value:
            raise ValidationFailed(
                "Upload failed verification",
                code="upload_rejected",
                details={"reason": attachment.processing_error},
            )
        return attachment  # idempotent: completing twice is fine

    storage = get_storage()
    info = await storage.head(attachment.storage_key)
    if info is None:
        raise ValidationFailed("No content was uploaded for this attachment", code="upload_missing")
    if info.size != attachment.file_size:
        raise ValidationFailed(
            "Uploaded size does not match the declared size",
            code="size_mismatch",
            details={"declared": attachment.file_size, "received": info.size},
        )

    digest = hashlib.sha256()
    prefix = bytearray()
    whole = bytearray() if attachment.declared_mime_type in _OOXML else None
    async for chunk in storage.open_stream(attachment.storage_key):
        digest.update(chunk)
        if len(prefix) < _SNIFF_BYTES:
            prefix.extend(chunk[: _SNIFF_BYTES - len(prefix)])
        if whole is not None:
            whole.extend(chunk)

    reason = None
    if digest.hexdigest() != attachment.checksum:
        reason = "checksum_mismatch"
    else:
        actual = _sniff(bytes(prefix), attachment.declared_mime_type)
        if actual is None or (
            whole is not None and not _verify_ooxml(bytes(whole), attachment.declared_mime_type)
        ):
            reason = "content_type_mismatch"

    if reason == "checksum_mismatch":
        # Probably a broken transfer: let the client upload again.
        await storage.delete(attachment.storage_key)
        raise ValidationFailed(
            "Checksum of the uploaded content does not match", code="checksum_mismatch"
        )
    if reason is not None:
        await storage.delete(attachment.storage_key)
        attachment.processing_status = ProcessingStatus.failed.value
        attachment.processing_error = reason
        await db.commit()
        raise ValidationFailed(
            "File content does not match its declared type", code="content_type_mismatch"
        )

    attachment.processing_status = ProcessingStatus.uploaded.value
    attachment.uploaded_at = utcnow()
    await jobs.enqueue(
        db,
        PROCESS_JOB,
        {"attachment_id": str(attachment.id)},
        owner_id=owner_id,
        dedupe_key=f"process:{attachment.id}",
    )
    await db.commit()
    return attachment


async def delete_attachment(
    db: AsyncSession, owner_id: uuid.UUID, attachment_id: uuid.UUID
) -> None:
    """Unlink and mark deleted; the cleanup job removes the objects later."""
    attachment = await get_attachment(db, owner_id, attachment_id)
    attachment.deleted_at = utcnow()
    linked = list(
        (
            await db.execute(
                select(ThoughtAttachment.thought_id).where(
                    ThoughtAttachment.attachment_id == attachment.id
                )
            )
        ).scalars()
    )
    await db.execute(
        delete(ThoughtAttachment).where(ThoughtAttachment.attachment_id == attachment.id)
    )
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    await refresh_search_documents(db, owner_id, linked)
    await db.commit()


def content_disposition(filename: str, inline: bool) -> str:
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    ascii_name = ascii_name.replace('"', "")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
