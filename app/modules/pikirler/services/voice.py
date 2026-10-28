"""Voice thoughts: audio is stored first, transcription happens in the worker.

The ASR output (``raw_text``) is never overwritten; the owner's corrections go
to ``edited_text``. The transcript never replaces the thought's own text.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.modules.pikirler.integrations.asr import get_transcription_provider
from app.modules.pikirler.integrations.embeddings import ensure_allowed
from app.modules.pikirler.models import Owner, ThoughtAttachment, VoiceTranscription
from app.modules.pikirler.models.enums import AttachmentKind, TranscriptionStatus
from app.modules.pikirler.repositories import thoughts as thought_repo
from app.modules.pikirler.services import jobs
from app.modules.pikirler.services.auth import Principal
from app.modules.pikirler.services.media import get_attachment
from app.storage import get_storage

TRANSCRIBE_JOB = "transcribe_audio"


async def request_transcription(
    db: AsyncSession,
    principal: Principal,
    attachment_id: uuid.UUID,
    language: str | None,
    thought_id: uuid.UUID | None,
) -> VoiceTranscription:
    provider = get_transcription_provider()  # 503 when ASR is disabled: never fake success
    ensure_allowed(provider, principal.ai_external_enabled)
    attachment = await get_attachment(db, principal.owner_id, attachment_id)
    if not is_audio_kind(attachment.kind):
        raise ValidationFailed("Only audio or video can be transcribed", code="not_audio")
    if attachment.uploaded_at is None:
        raise ValidationFailed("Attachment upload is not complete", code="attachment_not_uploaded")
    if thought_id is not None:
        await thought_repo.get(db, principal.owner_id, thought_id)

    active = (
        await db.execute(
            select(VoiceTranscription).where(
                VoiceTranscription.attachment_id == attachment.id,
                VoiceTranscription.status.in_(
                    [TranscriptionStatus.queued.value, TranscriptionStatus.processing.value]
                ),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        return active  # repeated request while one is in flight

    vt = VoiceTranscription(
        owner_id=principal.owner_id,
        attachment_id=attachment.id,
        thought_id=thought_id,
        requested_language=language,
        status=TranscriptionStatus.queued.value,
        provider=provider.name,
        model=provider.model,
    )
    db.add(vt)
    await db.flush()
    vt.job_id = await jobs.enqueue(
        db,
        TRANSCRIBE_JOB,
        {"transcription_id": str(vt.id)},
        owner_id=principal.owner_id,
        dedupe_key=f"asr:{vt.id}",
    )
    await db.commit()
    return vt


async def get_transcription(
    db: AsyncSession, owner_id: uuid.UUID, transcription_id: uuid.UUID
) -> VoiceTranscription:
    vt = (
        await db.execute(
            select(VoiceTranscription).where(
                VoiceTranscription.id == transcription_id, VoiceTranscription.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()
    if vt is None:
        raise NotFound("Transcription not found", code="transcription_not_found")
    return vt


async def _thoughts_using(db: AsyncSession, vt: VoiceTranscription) -> list[uuid.UUID]:
    ids = set(
        (
            await db.execute(
                select(ThoughtAttachment.thought_id).where(
                    ThoughtAttachment.attachment_id == vt.attachment_id
                )
            )
        ).scalars()
    )
    if vt.thought_id:
        ids.add(vt.thought_id)
    return list(ids)


async def edit_transcription(
    db: AsyncSession, owner_id: uuid.UUID, transcription_id: uuid.UUID, edited_text: str | None
) -> VoiceTranscription:
    vt = await get_transcription(db, owner_id, transcription_id)
    if vt.status not in (TranscriptionStatus.completed.value, TranscriptionStatus.failed.value):
        raise Conflict("Transcription is still running", code="transcription_in_progress")
    vt.edited_text = edited_text
    vt.edited_at = utcnow() if edited_text is not None else None
    await db.flush()
    await _after_text_change(db, vt)
    await db.commit()
    return vt


async def _after_text_change(db: AsyncSession, vt: VoiceTranscription) -> None:
    from app.modules.pikirler.services import thoughts as thought_service

    ids = await _thoughts_using(db, vt)
    await thought_service.refresh_search_documents(db, vt.owner_id, ids)
    for thought in (await thought_repo.load_many(db, vt.owner_id, ids)).values():
        await thought_service._queue_embedding(db, thought)


# --- worker side -------------------------------------------------------------


async def run_transcription(db: AsyncSession, transcription_id: uuid.UUID) -> dict[str, Any]:
    vt = await db.get(VoiceTranscription, transcription_id)
    if vt is None:
        return {"skipped": "transcription gone"}
    if vt.status == TranscriptionStatus.completed.value:
        return {"skipped": "already completed"}
    owner = await db.get(Owner, vt.owner_id)
    attachment = await get_attachment(db, vt.owner_id, vt.attachment_id)
    provider = get_transcription_provider()
    ensure_allowed(provider, bool(owner and owner.ai_external_enabled))

    vt.status = TranscriptionStatus.processing.value
    vt.error = None
    await db.commit()

    suffix = os.path.splitext(attachment.safe_filename)[1][:10]
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        await get_storage().download_to(attachment.storage_key, path)
        result = await provider.transcribe(path, vt.requested_language)
    finally:
        os.unlink(path)

    vt.raw_text = result.text
    vt.detected_language = (result.language or "")[:8] or None
    vt.segments = result.segments
    vt.provider = provider.name
    vt.model = result.model
    vt.status = TranscriptionStatus.completed.value
    vt.completed_at = utcnow()
    await db.flush()
    await _after_text_change(db, vt)
    await db.commit()
    return {"status": "completed", "chars": len(result.text), "language": vt.detected_language}


async def mark_failed(db: AsyncSession, transcription_id: uuid.UUID, error: str) -> None:
    vt = await db.get(VoiceTranscription, transcription_id)
    if vt is not None and vt.status != TranscriptionStatus.completed.value:
        vt.status = TranscriptionStatus.failed.value
        vt.error = error[:2000]
        await db.commit()


def is_audio_kind(kind: str) -> bool:
    return kind in (AttachmentKind.audio.value, AttachmentKind.video.value)
