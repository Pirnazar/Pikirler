"""Turn loaded ORM thoughts into API objects with a constant number of queries."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.pikirler.models import Attachment, Thought, VoiceTranscription
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.schemas.thoughts import (
    AttachmentRef,
    ProjectRef,
    TagRef,
    ThoughtOut,
    TranscriptionRef,
)


def media_url(attachment_id: object, variant: str = "content") -> str:
    return f"{get_settings().api_prefix}/media/{attachment_id}/{variant}"


def attachment_ref(att: Attachment, vt: VoiceTranscription | None = None) -> AttachmentRef:
    transcription = None
    if vt is not None:
        transcription = TranscriptionRef(
            id=vt.id,
            status=vt.status,
            text=vt.edited_text or vt.raw_text,
            language=vt.detected_language or vt.requested_language,
        )
    return AttachmentRef(
        id=att.id,
        kind=att.kind,
        mime_type=att.mime_type,
        original_filename=att.original_filename,
        file_size=att.file_size,
        processing_status=att.processing_status,
        width=att.width,
        height=att.height,
        duration_seconds=att.duration_seconds,
        has_thumbnail=att.thumbnail_key is not None,
        content_url=media_url(att.id),
        thumbnail_url=media_url(att.id, "thumbnail") if att.thumbnail_key else None,
        transcription=transcription,
    )


async def present(db: AsyncSession, thoughts: Sequence[Thought]) -> list[ThoughtOut]:
    ids = [t.id for t in thoughts]
    counts = await repo.reply_counts(db, ids)
    attachment_ids = [link.attachment_id for t in thoughts for link in t.attachment_links]
    transcripts = await repo.latest_transcriptions(db, attachment_ids)
    out: list[ThoughtOut] = []
    for t in thoughts:
        tags = sorted(t.tag_links, key=lambda link: link.tag.normalized_name)
        out.append(
            ThoughtOut(
                id=t.id,
                content=t.content,
                content_format=t.content_format,
                thought_type=t.thought_type,
                status=t.status,
                visibility=t.visibility,
                source=t.source,
                parent_id=t.parent_id,
                thread_id=t.thread_id,
                is_bookmarked=t.is_bookmarked,
                is_pinned=t.is_pinned,
                occurred_at=t.occurred_at,
                created_at=t.created_at,
                updated_at=t.updated_at,
                deleted_at=t.deleted_at,
                version=t.version,
                tags=[link.tag.name for link in tags],
                tag_refs=[
                    TagRef(
                        id=link.tag.id, name=link.tag.name, normalized_name=link.tag.normalized_name
                    )
                    for link in tags
                ],
                projects=[
                    ProjectRef(
                        id=link.project.id, name=link.project.name, status=link.project.status
                    )
                    for link in t.project_links
                ],
                attachments=[
                    attachment_ref(link.attachment, transcripts.get(link.attachment_id))
                    for link in t.attachment_links
                    if link.attachment.deleted_at is None
                ],
                reply_count=counts.get(t.id, 0),
            )
        )
    return out


async def present_one(db: AsyncSession, thought: Thought) -> ThoughtOut:
    return (await present(db, [thought]))[0]
