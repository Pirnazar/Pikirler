from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, File, Form, UploadFile, status
from pydantic import Field

from app.api.deps import DB, VoiceWrite
from app.core.exceptions import Forbidden, PayloadTooLarge, ProviderUnavailable
from app.modules.pikirler.models import VoiceTranscription
from app.modules.pikirler.models.enums import AttachmentKind, ThoughtType
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.schemas.thoughts import ThoughtCreate, ThoughtOut
from app.modules.pikirler.services import media, presenter
from app.modules.pikirler.services import thoughts as thought_service
from app.modules.pikirler.services import voice as service

router = APIRouter(prefix="/voice", tags=["voice"], responses=ERROR_RESPONSES)

Language = Literal["tk", "ru", "en", "zh"]


class TranscriptionCreate(Schema):
    attachment_id: uuid.UUID
    language: Language | None = Field(
        default=None, description="Omit for mixed-language speech: the model detects it."
    )
    thought_id: uuid.UUID | None = None


class TranscriptionUpdate(Schema):
    edited_text: str | None = Field(
        max_length=200_000, description="Manual correction; null clears it"
    )


class TranscriptionOut(Schema):
    id: uuid.UUID
    job_id: uuid.UUID | None
    attachment_id: uuid.UUID
    thought_id: uuid.UUID | None
    status: str
    requested_language: str | None
    detected_language: str | None
    provider: str | None
    model: str | None
    raw_text: str | None = Field(description="Exactly what the ASR model produced")
    edited_text: str | None
    text: str | None = Field(default=None, description="edited_text if present, else raw_text")
    segments: list[dict[str, Any]]
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    edited_at: datetime | None
    ai_generated: bool = True


class VoiceNoteOut(Schema):
    thought: ThoughtOut
    transcription: TranscriptionOut | None
    transcription_unavailable: str | None = Field(
        default=None, description="Why no transcription was queued (the audio is saved anyway)"
    )


def _out(vt: VoiceTranscription) -> TranscriptionOut:
    out = TranscriptionOut.model_validate(vt)
    return out.model_copy(update={"text": vt.edited_text or vt.raw_text})


@router.post(
    "/transcriptions", response_model=TranscriptionOut, status_code=status.HTTP_202_ACCEPTED
)
async def create_transcription(
    body: TranscriptionCreate, principal: VoiceWrite, db: DB
) -> TranscriptionOut:
    vt = await service.request_transcription(
        db, principal, body.attachment_id, body.language, body.thought_id
    )
    return _out(vt)


@router.get("/transcriptions/{transcription_id}", response_model=TranscriptionOut)
async def get_transcription(
    transcription_id: uuid.UUID, principal: VoiceWrite, db: DB
) -> TranscriptionOut:
    return _out(await service.get_transcription(db, principal.owner_id, transcription_id))


@router.patch("/transcriptions/{transcription_id}", response_model=TranscriptionOut)
async def edit_transcription(
    transcription_id: uuid.UUID, body: TranscriptionUpdate, principal: VoiceWrite, db: DB
) -> TranscriptionOut:
    vt = await service.edit_transcription(
        db, principal.owner_id, transcription_id, body.edited_text
    )
    return _out(vt)


@router.post("/notes", response_model=VoiceNoteOut, status_code=status.HTTP_201_CREATED)
async def create_voice_note(
    principal: VoiceWrite,
    db: DB,
    file: UploadFile = File(description="Recorded audio"),
    language: Language | None = Form(default=None),
    content: str = Form(default="", max_length=200_000),
    thought_type: ThoughtType = Form(default=ThoughtType.thought),
    tags: str = Form(default="", description="Comma-separated"),
) -> VoiceNoteOut:
    """One call for the voice recorder and Jemşit: store the audio, create the
    thought with it attached, and queue the transcription."""
    limit = media.max_bytes_for(AttachmentKind.audio)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise PayloadTooLarge("Audio file too large", details={"max_bytes": limit})
    mime = (file.content_type or "application/octet-stream").split(";")[0]
    ticket = await media.create_upload(
        db,
        principal.owner_id,
        file.filename or "voice-note",
        mime,
        len(data),
        hashlib.sha256(data).hexdigest(),
    )

    async def body() -> AsyncIterator[bytes]:
        yield data

    if ticket.attachment.uploaded_at is None:
        await media.receive_content(db, principal.owner_id, ticket.attachment.id, body())
        await media.complete_upload(db, principal.owner_id, ticket.attachment.id)
    if not service.is_audio_kind(ticket.attachment.kind):
        from app.core.exceptions import ValidationFailed

        raise ValidationFailed("Voice notes must be audio", code="not_audio")
    thought = await thought_service.create(
        db,
        principal,
        ThoughtCreate(
            content=content,
            thought_type=thought_type,
            attachment_ids=[ticket.attachment.id],
            tags=[t.strip() for t in tags.split(",") if t.strip()],
        ),
    )
    try:
        vt = await service.request_transcription(
            db, principal, ticket.attachment.id, language, thought.id
        )
    except (ProviderUnavailable, Forbidden) as exc:
        # The recording is never lost because ASR is off or not permitted.
        return VoiceNoteOut(
            thought=await presenter.present_one(db, thought),
            transcription=None,
            transcription_unavailable=exc.code,
        )
    return VoiceNoteOut(thought=await presenter.present_one(db, thought), transcription=_out(vt))
