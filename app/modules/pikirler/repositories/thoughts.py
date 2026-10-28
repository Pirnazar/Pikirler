"""Owner-scoped thought queries. Every query starts from ``scoped(owner_id)``."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFound
from app.core.pagination import Cursor
from app.core.text import normalize_tag
from app.modules.pikirler.models import (
    Tag,
    Thought,
    ThoughtAttachment,
    ThoughtProject,
    ThoughtTag,
    VoiceTranscription,
)


@dataclass
class ThoughtFilters:
    thought_type: str | None = None
    status: str | None = None
    statuses: Sequence[str] | None = None
    tag: str | None = None
    project_id: uuid.UUID | None = None
    bookmarked: bool | None = None
    pinned: bool | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    thread_id: uuid.UUID | None = None
    has_media: bool | None = None
    in_any_project: bool | None = None
    deleted: bool = False


def scoped(owner_id: uuid.UUID) -> Select[tuple[Thought]]:
    return select(Thought).where(Thought.owner_id == owner_id)


def with_relations(stmt: Select[tuple[Thought]]) -> Select[tuple[Thought]]:
    # One extra query per relationship level, independent of page size: no N+1.
    return stmt.options(
        selectinload(Thought.tag_links).selectinload(ThoughtTag.tag),
        selectinload(Thought.project_links).selectinload(ThoughtProject.project),
        selectinload(Thought.attachment_links).selectinload(ThoughtAttachment.attachment),
    )


def apply_filters(
    stmt: Select[tuple[Thought]], owner_id: uuid.UUID, f: ThoughtFilters
) -> Select[tuple[Thought]]:
    stmt = stmt.where(Thought.deleted_at.isnot(None) if f.deleted else Thought.deleted_at.is_(None))
    if f.thought_type:
        stmt = stmt.where(Thought.thought_type == f.thought_type)
    if f.status:
        stmt = stmt.where(Thought.status == f.status)
    if f.statuses:
        stmt = stmt.where(Thought.status.in_(list(f.statuses)))
    if f.bookmarked is not None:
        stmt = stmt.where(Thought.is_bookmarked.is_(f.bookmarked))
    if f.pinned is not None:
        stmt = stmt.where(Thought.is_pinned.is_(f.pinned))
    if f.date_from:
        stmt = stmt.where(Thought.created_at >= f.date_from)
    if f.date_to:
        stmt = stmt.where(Thought.created_at < f.date_to)
    if f.thread_id:
        stmt = stmt.where(Thought.thread_id == f.thread_id)
    if f.tag:
        stmt = stmt.where(
            exists().where(
                ThoughtTag.thought_id == Thought.id,
                ThoughtTag.tag_id == Tag.id,
                Tag.owner_id == owner_id,
                Tag.normalized_name == normalize_tag(f.tag),
            )
        )
    if f.project_id:
        stmt = stmt.where(
            exists().where(
                ThoughtProject.thought_id == Thought.id,
                ThoughtProject.project_id == f.project_id,
                ThoughtProject.owner_id == owner_id,
            )
        )
    if f.in_any_project:
        stmt = stmt.where(exists().where(ThoughtProject.thought_id == Thought.id))
    if f.has_media:
        stmt = stmt.where(exists().where(ThoughtAttachment.thought_id == Thought.id))
    return stmt


async def page(
    db: AsyncSession, owner_id: uuid.UUID, f: ThoughtFilters, limit: int, cursor: str | None
) -> tuple[list[Thought], str | None]:
    stmt = apply_filters(scoped(owner_id), owner_id, f)
    if cursor:
        c = Cursor.decode(cursor)
        stmt = stmt.where(
            tuple_(Thought.created_at, Thought.id) < tuple_(literal(c.created_at), literal(c.id))
        )
    stmt = (
        with_relations(stmt).order_by(Thought.created_at.desc(), Thought.id.desc()).limit(limit + 1)
    )
    rows = list((await db.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = Cursor(last.created_at, last.id).encode()
    return rows, next_cursor


async def get(
    db: AsyncSession,
    owner_id: uuid.UUID,
    thought_id: uuid.UUID,
    *,
    include_deleted: bool = False,
    load: bool = False,
    for_update: bool = False,
) -> Thought:
    stmt = scoped(owner_id).where(Thought.id == thought_id)
    if not include_deleted:
        stmt = stmt.where(Thought.deleted_at.is_(None))
    if load:
        stmt = with_relations(stmt)
    if for_update:
        stmt = stmt.with_for_update(of=Thought)
    thought = (await db.execute(stmt)).scalar_one_or_none()
    if thought is None:
        # Same answer for "missing" and "someone else's": no existence oracle.
        raise NotFound("Thought not found", code="thought_not_found")
    return thought


async def load_many(
    db: AsyncSession, owner_id: uuid.UUID, ids: Sequence[uuid.UUID], include_deleted: bool = False
) -> dict[uuid.UUID, Thought]:
    if not ids:
        return {}
    stmt = with_relations(scoped(owner_id).where(Thought.id.in_(list(ids))))
    if not include_deleted:
        stmt = stmt.where(Thought.deleted_at.is_(None))
    return {t.id: t for t in (await db.execute(stmt)).scalars()}


async def reply_counts(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Thought.parent_id, func.count())
        .where(Thought.parent_id.in_(list(ids)), Thought.deleted_at.is_(None))
        .group_by(Thought.parent_id)
    )
    return {pid: int(n) for pid, n in rows.all()}


async def latest_transcriptions(
    db: AsyncSession, attachment_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, VoiceTranscription]:
    if not attachment_ids:
        return {}
    rows = await db.execute(
        select(VoiceTranscription)
        .where(VoiceTranscription.attachment_id.in_(list(attachment_ids)))
        .order_by(VoiceTranscription.attachment_id, VoiceTranscription.created_at.desc())
        .distinct(VoiceTranscription.attachment_id)
    )
    return {vt.attachment_id: vt for vt in rows.scalars()}


async def owned_ids(
    db: AsyncSession, owner_id: uuid.UUID, ids: Sequence[uuid.UUID]
) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = await db.execute(
        select(Thought.id).where(
            Thought.owner_id == owner_id, Thought.id.in_(list(ids)), Thought.deleted_at.is_(None)
        )
    )
    return set(rows.scalars())


def not_deleted_or(*conditions):  # type: ignore[no-untyped-def]
    return and_(Thought.deleted_at.is_(None), or_(*conditions))
