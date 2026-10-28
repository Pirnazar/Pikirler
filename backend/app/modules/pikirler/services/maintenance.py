"""Periodic housekeeping run by the worker's cron."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.modules.pikirler.models import (
    Attachment,
    AuthSession,
    Job,
    Thought,
    ThoughtAttachment,
    VoiceTranscription,
)
from app.modules.pikirler.models.enums import JobStatus, ProcessingStatus
from app.storage import get_storage

log = logging.getLogger("pikirler.maintenance")

ORPHAN_GRACE = timedelta(days=7)


async def _delete_objects(att: Attachment) -> None:
    storage = get_storage()
    for key in (att.storage_key, att.preview_key, att.thumbnail_key):
        if key:
            await storage.delete(key)


async def cleanup_media(db: AsyncSession, batch: int = 500) -> dict[str, int]:
    """Remove storage objects nobody can reach any more:
    abandoned uploads, deleted attachments past retention, and uploads that
    were never attached to a thought or a transcription."""
    s = get_settings()
    now = utcnow()
    abandoned = (Attachment.processing_status == ProcessingStatus.pending_upload.value) & (
        Attachment.created_at < now - timedelta(hours=s.upload_session_ttl_hours)
    )
    rejected = Attachment.processing_status == ProcessingStatus.failed.value
    expired = Attachment.deleted_at < now - timedelta(days=s.soft_delete_retention_days)
    orphan = and_(
        Attachment.deleted_at.is_(None),
        Attachment.uploaded_at < now - ORPHAN_GRACE,
        ~exists().where(ThoughtAttachment.attachment_id == Attachment.id),
        ~exists().where(VoiceTranscription.attachment_id == Attachment.id),
    )
    rows = list(
        (
            await db.execute(
                select(Attachment).where(or_(abandoned, rejected, expired, orphan)).limit(batch)
            )
        ).scalars()
    )
    for att in rows:
        await _delete_objects(att)
        await db.delete(att)
    await db.commit()
    return {"attachments_removed": len(rows)}


async def purge_deleted_thoughts(db: AsyncSession, batch: int = 500) -> dict[str, int]:
    """Hard-delete thoughts that stayed in the trash past the retention period."""
    cutoff = utcnow() - timedelta(days=get_settings().soft_delete_retention_days)
    ids = list(
        (
            await db.execute(select(Thought.id).where(Thought.deleted_at < cutoff).limit(batch))
        ).scalars()
    )
    if ids:
        # Replies outlive their purged parent; they keep their thread.
        await db.execute(update(Thought).where(Thought.parent_id.in_(ids)).values(parent_id=None))
        await db.execute(delete(Thought).where(Thought.id.in_(ids)))
    await db.commit()
    return {"thoughts_purged": len(ids)}


async def prune_bookkeeping(db: AsyncSession) -> dict[str, Any]:
    now = utcnow()
    sessions = await db.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.expires_at < now - timedelta(days=30),
                AuthSession.revoked_at < now - timedelta(days=30),
            )
        )
    )
    finished = await db.execute(
        delete(Job).where(
            Job.status == JobStatus.succeeded.value, Job.finished_at < now - timedelta(days=30)
        )
    )
    await db.commit()
    return {"sessions": sessions.rowcount, "jobs": finished.rowcount}  # type: ignore[attr-defined]
