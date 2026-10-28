"""Looking back: on this day, random memory, monthly recap, activity calendar,
idea evolution and forgotten ideas.

Timestamps are stored in UTC; every calendar computation converts to the
owner's configured timezone first. ``created_at`` (when it was written) is used
throughout; ``occurred_at`` (what it is about) is returned alongside.
"""

from __future__ import annotations

import uuid
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, extract, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import ValidationFailed
from app.modules.pikirler.models import Tag, Thought, ThoughtRelation, ThoughtTag
from app.modules.pikirler.models.enums import RelationStatus, ThoughtStatus, ThoughtType
from app.modules.pikirler.repositories import thoughts as repo

EVOLUTION_LIMIT = 200


def _local(column: Any, tz: str) -> Any:
    return func.timezone(tz, column)


def _live(owner_id: uuid.UUID) -> Any:
    return repo.scoped(owner_id).where(
        Thought.deleted_at.is_(None), Thought.status == ThoughtStatus.published.value
    )


def local_today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


async def on_this_day(
    db: AsyncSession, owner_id: uuid.UUID, tz: str, day: date | None = None
) -> tuple[date, list[Thought]]:
    day = day or local_today(tz)
    local = _local(Thought.created_at, tz)
    stmt = repo.with_relations(
        _live(owner_id)
        .where(
            extract("month", local) == day.month,
            extract("day", local) == day.day,
            extract("year", local) < day.year,
        )
        .order_by(Thought.created_at.desc())
    ).limit(100)
    return day, list((await db.execute(stmt)).scalars())


async def random_memory(
    db: AsyncSession, owner_id: uuid.UUID, thought_type: str | None
) -> Thought | None:
    stmt = _live(owner_id)
    if thought_type:
        stmt = stmt.where(Thought.thought_type == thought_type)
    row = (await db.execute(stmt.order_by(func.random()).limit(1))).scalar_one_or_none()
    if row is None:
        return None
    return await repo.get(db, owner_id, row.id, load=True)


def month_bounds(year: int, month: int, tz: str) -> tuple[datetime, datetime]:
    if not 1 <= month <= 12 or not 1970 <= year <= 9999:
        raise ValidationFailed("Invalid year or month", code="invalid_period")
    zone = ZoneInfo(tz)
    start = datetime(year, month, 1, tzinfo=zone)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=zone)
    return start, end


async def monthly(
    db: AsyncSession, owner_id: uuid.UUID, tz: str, year: int, month: int
) -> dict[str, Any]:
    start, end = month_bounds(year, month, tz)
    in_month = (Thought.created_at >= start, Thought.created_at < end)
    base = _live(owner_id).where(*in_month).subquery()
    by_type: dict[str, int] = {
        row[0]: row[1]
        for row in (
            await db.execute(
                select(base.c.thought_type, func.count()).group_by(base.c.thought_type)
            )
        ).all()
    }
    local_day = cast(_local(base.c.created_at, tz), Date)
    busiest = (
        await db.execute(
            select(local_day, func.count())
            .group_by(local_day)
            .order_by(func.count().desc())
            .limit(1)
        )
    ).first()
    top_tags = (
        await db.execute(
            select(Tag.name, func.count())
            .join(ThoughtTag, ThoughtTag.tag_id == Tag.id)
            .join(base, base.c.id == ThoughtTag.thought_id)
            .group_by(Tag.name)
            .order_by(func.count().desc(), Tag.name)
            .limit(10)
        )
    ).all()
    highlights = list(
        (
            await db.execute(
                repo.with_relations(
                    _live(owner_id)
                    .where(*in_month)
                    .order_by(
                        Thought.is_pinned.desc(),
                        Thought.is_bookmarked.desc(),
                        Thought.created_at.desc(),
                    )
                    .limit(30)
                )
            )
        ).scalars()
    )
    return {
        "year": year,
        "month": month,
        "timezone": tz,
        "total": sum(by_type.values()),
        "by_type": by_type,
        "busiest_day": {"date": busiest[0], "count": busiest[1]} if busiest else None,
        "top_tags": [{"name": n, "count": c} for n, c in top_tags],
        "highlights": highlights,
    }


async def activity(
    db: AsyncSession, owner_id: uuid.UUID, tz: str, year: int
) -> list[dict[str, Any]]:
    start, _ = month_bounds(year, 1, tz)
    _, end = month_bounds(year, 12, tz)
    local_day = cast(_local(Thought.created_at, tz), Date)
    rows = (
        await db.execute(
            select(local_day.label("day"), func.count())
            .where(
                Thought.owner_id == owner_id,
                Thought.deleted_at.is_(None),
                Thought.status == ThoughtStatus.published.value,
                Thought.created_at >= start,
                Thought.created_at < end,
            )
            .group_by("day")
            .order_by("day")
        )
    ).all()
    return [{"date": d, "count": int(n)} for d, n in rows]


async def evolution(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID
) -> tuple[list[Thought], list[ThoughtRelation]]:
    """The idea's whole lineage: its thread plus everything reachable through
    owner-confirmed relations (AI suggestions are not followed), oldest first."""
    start = await repo.get(db, owner_id, thought_id)
    seen: set[uuid.UUID] = {start.id}
    edges: list[ThoughtRelation] = []
    queue: deque[uuid.UUID] = deque([start.id])
    if start.thread_id:
        for tid in (
            await db.execute(
                select(Thought.id).where(
                    Thought.thread_id == start.thread_id, Thought.deleted_at.is_(None)
                )
            )
        ).scalars():
            if tid not in seen:
                seen.add(tid)
                queue.append(tid)
    while queue and len(seen) < EVOLUTION_LIMIT:
        current = queue.popleft()
        rels = (
            (
                await db.execute(
                    select(ThoughtRelation).where(
                        ThoughtRelation.owner_id == owner_id,
                        ThoughtRelation.status == RelationStatus.confirmed.value,
                        or_(
                            ThoughtRelation.source_thought_id == current,
                            ThoughtRelation.target_thought_id == current,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for rel in rels:
            if rel not in edges:
                edges.append(rel)
            other = (
                rel.target_thought_id if rel.source_thought_id == current else rel.source_thought_id
            )
            if other not in seen and len(seen) < EVOLUTION_LIMIT:
                seen.add(other)
                queue.append(other)
    thoughts = await repo.load_many(db, owner_id, list(seen))
    ordered = sorted(thoughts.values(), key=lambda t: (t.created_at, t.id))
    return ordered, [
        e for e in edges if e.source_thought_id in thoughts and e.target_thought_id in thoughts
    ]


async def forgotten(
    db: AsyncSession, owner_id: uuid.UUID, older_than_days: int, limit: int
) -> list[Thought]:
    """Ideas nobody came back to: no edits, no replies, no relations for a long time."""
    cutoff = utcnow() - timedelta(days=older_than_days)
    replies = select(Thought.parent_id).where(
        Thought.parent_id.isnot(None), Thought.deleted_at.is_(None)
    )
    stmt = repo.with_relations(
        _live(owner_id)
        .where(
            Thought.thought_type == ThoughtType.idea.value,
            Thought.updated_at < cutoff,
            Thought.id.not_in(replies),
            Thought.thread_id.is_(None),
        )
        .order_by(func.random())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())
