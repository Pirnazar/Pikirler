"""Threads: chains of the owner's own thoughts."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFound
from app.modules.pikirler.models import Thought, Thread
from app.modules.pikirler.repositories import thoughts as repo


async def get_thread(db: AsyncSession, owner_id: uuid.UUID, thread_id: uuid.UUID) -> Thread:
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id, Thread.owner_id == owner_id))
    ).scalar_one_or_none()
    if thread is None:
        raise NotFound("Thread not found", code="thread_not_found")
    return thread


async def thread_thoughts(
    db: AsyncSession, owner_id: uuid.UUID, thread_id: uuid.UUID
) -> list[Thought]:
    """All live thoughts of the thread, oldest first."""
    stmt = repo.with_relations(
        repo.scoped(owner_id)
        .where(Thought.thread_id == thread_id, Thought.deleted_at.is_(None))
        .order_by(Thought.created_at, Thought.id)
    )
    return list((await db.execute(stmt)).scalars())


def depths(thoughts: list[Thought]) -> dict[uuid.UUID, int]:
    """Depth below the root; a reply whose parent was deleted counts from the
    nearest live ancestor known in the list."""
    by_id = {t.id: t for t in thoughts}
    out: dict[uuid.UUID, int] = {}

    def depth(t: Thought) -> int:
        if t.id in out:
            return out[t.id]
        parent = by_id.get(t.parent_id) if t.parent_id else None
        value = 0 if parent is None else depth(parent) + 1
        out[t.id] = value
        return value

    for t in thoughts:
        depth(t)
    return out


async def rename(
    db: AsyncSession, owner_id: uuid.UUID, thread_id: uuid.UUID, title: str | None
) -> Thread:
    thread = await get_thread(db, owner_id, thread_id)
    thread.title = title
    await db.commit()
    return thread
