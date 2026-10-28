"""Thought lifecycle: create, reply, edit (optimistic locking + revisions),
soft delete/restore, bookmarks, pins."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.core.text import build_search_document
from app.modules.pikirler.models import (
    Attachment,
    Project,
    Tag,
    Thought,
    ThoughtAttachment,
    ThoughtProject,
    ThoughtRevision,
    ThoughtTag,
    Thread,
    VoiceTranscription,
)
from app.modules.pikirler.models.enums import ProcessingStatus, RevisionSource, ThoughtStatus
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.schemas.thoughts import ThoughtCreate, ThoughtUpdate
from app.modules.pikirler.services import jobs, tags
from app.modules.pikirler.services.auth import Principal

EMBED_JOB = "embed_thought"
LINKABLE_STATUSES = (
    ProcessingStatus.uploaded.value,
    ProcessingStatus.processing.value,
    ProcessingStatus.ready.value,
)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _source_for(principal: Principal, requested: str | None) -> str:
    if principal.kind == "service":
        return principal.source
    return requested or "web"


async def _validate_projects(
    db: AsyncSession, owner_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    unique = list(dict.fromkeys(project_ids))
    if not unique:
        return []
    found = set(
        (
            await db.execute(
                select(Project.id).where(Project.owner_id == owner_id, Project.id.in_(unique))
            )
        ).scalars()
    )
    missing = [str(pid) for pid in unique if pid not in found]
    if missing:
        raise NotFound("Project not found", code="project_not_found", details={"ids": missing})
    return unique


async def _validate_attachments(
    db: AsyncSession, owner_id: uuid.UUID, attachment_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    unique = list(dict.fromkeys(attachment_ids))
    if not unique:
        return []
    rows = (
        await db.execute(
            select(Attachment.id, Attachment.processing_status).where(
                Attachment.owner_id == owner_id,
                Attachment.id.in_(unique),
                Attachment.deleted_at.is_(None),
            )
        )
    ).all()
    status = {r.id: r.processing_status for r in rows}
    missing = [str(a) for a in unique if a not in status]
    if missing:
        raise NotFound(
            "Attachment not found", code="attachment_not_found", details={"ids": missing}
        )
    incomplete = [str(a) for a in unique if status[a] not in LINKABLE_STATUSES]
    if incomplete:
        raise ValidationFailed(
            "Attachment upload is not complete",
            code="attachment_not_uploaded",
            details={"ids": incomplete},
        )
    return unique


def _check_publishable(status: str, content: str, attachment_count: int) -> None:
    if status == ThoughtStatus.published.value and not content.strip() and attachment_count == 0:
        raise ValidationFailed(
            "A published thought needs text or at least one attachment", code="empty_thought"
        )


async def _set_projects(db: AsyncSession, thought: Thought, project_ids: list[uuid.UUID]) -> None:
    await db.execute(delete(ThoughtProject).where(ThoughtProject.thought_id == thought.id))
    if project_ids:
        await db.execute(
            insert(ThoughtProject).values(
                [
                    {"thought_id": thought.id, "project_id": pid, "owner_id": thought.owner_id}
                    for pid in project_ids
                ]
            )
        )


async def _set_attachments(
    db: AsyncSession, thought: Thought, attachment_ids: list[uuid.UUID]
) -> None:
    await db.execute(delete(ThoughtAttachment).where(ThoughtAttachment.thought_id == thought.id))
    if attachment_ids:
        await db.execute(
            insert(ThoughtAttachment).values(
                [
                    {
                        "thought_id": thought.id,
                        "attachment_id": aid,
                        "owner_id": thought.owner_id,
                        "position": i,
                    }
                    for i, aid in enumerate(attachment_ids)
                ]
            )
        )


async def transcripts_for(
    db: AsyncSession, thought_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Completed voice transcripts (edited text preferred) of attached audio."""
    out: dict[uuid.UUID, list[str]] = {}
    if not thought_ids:
        return out
    for tid, text in (
        await db.execute(
            select(
                ThoughtAttachment.thought_id,
                func.coalesce(VoiceTranscription.edited_text, VoiceTranscription.raw_text),
            )
            .join(
                VoiceTranscription,
                VoiceTranscription.attachment_id == ThoughtAttachment.attachment_id,
            )
            .where(
                ThoughtAttachment.thought_id.in_(list(thought_ids)),
                VoiceTranscription.status == "completed",
            )
            .order_by(ThoughtAttachment.position)
        )
    ).all():
        if text:
            out.setdefault(tid, []).append(text)
    return out


async def refresh_search_documents(
    db: AsyncSession, owner_id: uuid.UUID, thought_ids: Sequence[uuid.UUID]
) -> None:
    """Rebuild search_document from content + tag names + voice transcripts."""
    ids = list(dict.fromkeys(thought_ids))
    if not ids:
        return
    contents: dict[uuid.UUID, str] = {
        row[0]: row[1]
        for row in (
            await db.execute(
                select(Thought.id, Thought.content).where(
                    Thought.owner_id == owner_id, Thought.id.in_(ids)
                )
            )
        ).all()
    }
    tag_names: dict[uuid.UUID, list[str]] = {}
    for tid, name in (
        await db.execute(
            select(ThoughtTag.thought_id, Tag.name)
            .join(Tag, Tag.id == ThoughtTag.tag_id)
            .where(ThoughtTag.thought_id.in_(ids))
        )
    ).all():
        tag_names.setdefault(tid, []).append(name)
    transcripts = await transcripts_for(db, ids)
    for tid, content in contents.items():
        doc = build_search_document(
            content, " ".join(tag_names.get(tid, [])), "\n".join(transcripts.get(tid, []))
        )
        await db.execute(update(Thought).where(Thought.id == tid).values(search_document=doc))


async def _queue_embedding(db: AsyncSession, thought: Thought) -> None:
    from app.modules.pikirler.integrations.embeddings import embeddings_enabled

    if embeddings_enabled():
        await jobs.enqueue(
            db,
            EMBED_JOB,
            {"thought_id": str(thought.id)},
            owner_id=thought.owner_id,
            dedupe_key=f"embed:{thought.id}",
        )


async def _next_revision_number(db: AsyncSession, thought_id: uuid.UUID) -> int:
    current = (
        await db.execute(
            select(func.max(ThoughtRevision.revision_number)).where(
                ThoughtRevision.thought_id == thought_id
            )
        )
    ).scalar_one()
    return int(current or 0) + 1


async def create(
    db: AsyncSession,
    principal: Principal,
    data: ThoughtCreate,
    *,
    parent: Thought | None = None,
    created_at: Any = None,
) -> Thought:
    owner_id = principal.owner_id
    project_ids = await _validate_projects(db, owner_id, data.project_ids)
    attachment_ids = await _validate_attachments(db, owner_id, data.attachment_ids)
    _check_publishable(data.status.value, data.content, len(attachment_ids))

    now = utcnow()
    thread_id: uuid.UUID | None = None
    if parent is not None:
        if parent.thread_id is None:
            thread = Thread(owner_id=owner_id, root_thought_id=parent.id, last_activity_at=now)
            db.add(thread)
            await db.flush()
            await db.execute(
                update(Thought).where(Thought.id == parent.id).values(thread_id=thread.id)
            )
            parent.thread_id = thread.id
        else:
            await db.execute(
                update(Thread).where(Thread.id == parent.thread_id).values(last_activity_at=now)
            )
        thread_id = parent.thread_id

    thought = Thought(
        owner_id=owner_id,
        content=data.content,
        content_format=data.content_format.value,
        thought_type=data.thought_type.value,
        status=data.status.value,
        source=_source_for(principal, data.source),
        source_metadata={"client": principal.extra["client"]}
        if "client" in principal.extra
        else {},
        parent_id=parent.id if parent else None,
        thread_id=thread_id,
        is_bookmarked=data.is_bookmarked,
        is_pinned=data.is_pinned,
        occurred_at=data.occurred_at,
        created_at=created_at or now,
        updated_at=created_at or now,
        version=1,
    )
    db.add(thought)
    await db.flush()
    await tags.sync_thought_tags(db, thought, data.tags)
    await _set_projects(db, thought, project_ids)
    await _set_attachments(db, thought, attachment_ids)
    db.add(
        ThoughtRevision(
            owner_id=owner_id,
            thought_id=thought.id,
            revision_number=1,
            previous_content=None,
            new_content=thought.content,
            change_source=RevisionSource.create.value,
        )
    )
    await refresh_search_documents(db, owner_id, [thought.id])
    await _queue_embedding(db, thought)
    await db.commit()
    return await repo.get(db, owner_id, thought.id, load=True)


async def reply(
    db: AsyncSession, principal: Principal, parent_id: uuid.UUID, data: ThoughtCreate
) -> Thought:
    parent = await repo.get(db, principal.owner_id, parent_id, for_update=True)
    return await create(db, principal, data, parent=parent)


async def update_thought(
    db: AsyncSession,
    principal: Principal,
    thought_id: uuid.UUID,
    data: ThoughtUpdate,
    *,
    change_source: RevisionSource = RevisionSource.edit,
) -> Thought:
    owner_id = principal.owner_id
    current = await repo.get(db, owner_id, thought_id, for_update=True)
    if current.version != data.expected_version:
        raise Conflict(
            "The thought was changed elsewhere; reload and retry",
            code="version_conflict",
            details={"current_version": current.version},
        )

    values: dict[str, Any] = {}
    fields = data.model_fields_set
    if data.content is not None and data.content != current.content:
        values["content"] = data.content
    for name in ("content_format", "thought_type", "status"):
        new = getattr(data, name)
        if new is not None and new.value != getattr(current, name):
            values[name] = new.value
    if data.clear_occurred_at:
        values["occurred_at"] = None
    elif "occurred_at" in fields and data.occurred_at is not None:
        values["occurred_at"] = data.occurred_at

    project_ids = (
        await _validate_projects(db, owner_id, data.project_ids)
        if data.project_ids is not None
        else None
    )
    attachment_ids = (
        await _validate_attachments(db, owner_id, data.attachment_ids)
        if data.attachment_ids is not None
        else None
    )
    if attachment_ids is None:
        attachment_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(ThoughtAttachment)
                    .where(ThoughtAttachment.thought_id == current.id)
                )
            ).scalar_one()
        )
    else:
        attachment_count = len(attachment_ids)
    _check_publishable(
        values.get("status", current.status),
        values.get("content", current.content),
        attachment_count,
    )

    relations_changed = (
        data.tags is not None or project_ids is not None or attachment_ids is not None
    )
    if not values and not relations_changed:
        return await repo.get(db, owner_id, thought_id, load=True)

    now = utcnow()
    previous_content = current.content  # the ORM UPDATE below syncs `current` in place
    # The row is locked FOR UPDATE above; the version predicate is a second
    # guard so a stale writer can never overwrite a newer version.
    result = await db.execute(
        update(Thought)
        .where(Thought.id == current.id, Thought.version == data.expected_version)
        .values(**values, version=Thought.version + 1, updated_at=now)
        .returning(Thought.version)
    )
    if result.scalar_one_or_none() is None:
        raise Conflict(
            "The thought was changed elsewhere; reload and retry", code="version_conflict"
        )
    if "content" in values:
        db.add(
            ThoughtRevision(
                owner_id=owner_id,
                thought_id=current.id,
                revision_number=await _next_revision_number(db, current.id),
                previous_content=previous_content,
                new_content=values["content"],
                change_source=change_source.value,
            )
        )
    await db.refresh(current)
    if data.tags is not None or "content" in values:
        await tags.sync_thought_tags(db, current, data.tags)
    if project_ids is not None:
        await _set_projects(db, current, project_ids)
    if attachment_ids is not None:
        await _set_attachments(db, current, attachment_ids)
    await refresh_search_documents(db, owner_id, [current.id])
    if "content" in values or data.tags is not None:
        await _queue_embedding(db, current)
    await db.commit()
    return await repo.get(db, owner_id, thought_id, load=True)


async def soft_delete(db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID) -> None:
    thought = await repo.get(db, owner_id, thought_id, for_update=True)
    thought.deleted_at = utcnow()
    thought.is_pinned = False
    await db.commit()


async def restore(db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID) -> Thought:
    thought = await repo.get(db, owner_id, thought_id, include_deleted=True, for_update=True)
    if thought.deleted_at is None:
        raise Conflict("Thought is not deleted", code="not_deleted")
    thought.deleted_at = None
    await db.commit()
    return await repo.get(db, owner_id, thought_id, load=True)


async def set_flag(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID, flag: str, value: bool
) -> Thought:
    assert flag in ("is_bookmarked", "is_pinned")
    result = await db.execute(
        update(Thought)
        .where(Thought.id == thought_id, Thought.owner_id == owner_id, Thought.deleted_at.is_(None))
        .values({flag: value})
        .returning(Thought.id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFound("Thought not found", code="thought_not_found")
    await db.commit()
    return await repo.get(db, owner_id, thought_id, load=True)


async def list_revisions(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID
) -> list[ThoughtRevision]:
    await repo.get(db, owner_id, thought_id, include_deleted=True)
    rows = await db.execute(
        select(ThoughtRevision)
        .where(ThoughtRevision.thought_id == thought_id, ThoughtRevision.owner_id == owner_id)
        .order_by(ThoughtRevision.revision_number.desc())
    )
    return list(rows.scalars())


async def restore_revision(
    db: AsyncSession,
    principal: Principal,
    thought_id: uuid.UUID,
    revision_id: uuid.UUID,
    expected_version: int | None,
) -> Thought:
    """Make the content of ``revision_id`` current again, as a new revision."""
    owner_id = principal.owner_id
    revision = (
        await db.execute(
            select(ThoughtRevision).where(
                ThoughtRevision.id == revision_id,
                ThoughtRevision.thought_id == thought_id,
                ThoughtRevision.owner_id == owner_id,
            )
        )
    ).scalar_one_or_none()
    if revision is None:
        raise NotFound("Revision not found", code="revision_not_found")
    current = await repo.get(db, owner_id, thought_id)
    if current.content == revision.new_content:
        return await repo.get(db, owner_id, thought_id, load=True)
    update_data = ThoughtUpdate(
        expected_version=expected_version or current.version, content=revision.new_content
    )
    return await update_thought(
        db, principal, thought_id, update_data, change_source=RevisionSource.restore
    )
