"""Tags: hashtag extraction, upsert, autocomplete, rename and merge."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.core.text import display_tag, escape_like, extract_hashtags, normalize_tag
from app.modules.pikirler.models import Tag, Thought, ThoughtTag
from app.modules.pikirler.models.enums import TagOrigin


async def upsert_tags(db: AsyncSession, owner_id: uuid.UUID, names: list[str]) -> dict[str, Tag]:
    """Return {normalized_name: Tag}, creating missing tags. Existing tags keep
    their original spelling."""
    wanted: dict[str, str] = {}
    for name in names:
        key = normalize_tag(name)
        if not key or len(key) > 64:
            raise ValidationFailed("Tags must be 1-64 characters", code="invalid_tag")
        wanted.setdefault(key, display_tag(name))
    if not wanted:
        return {}
    await db.execute(
        insert(Tag)
        .values(
            [
                {"id": uuid.uuid4(), "owner_id": owner_id, "name": display, "normalized_name": key}
                for key, display in wanted.items()
            ]
        )
        .on_conflict_do_nothing(constraint="uq_tags_owner_normalized")
    )
    rows = await db.execute(
        select(Tag).where(Tag.owner_id == owner_id, Tag.normalized_name.in_(list(wanted)))
    )
    return {tag.normalized_name: tag for tag in rows.scalars()}


async def sync_thought_tags(
    db: AsyncSession, thought: Thought, manual: list[str] | None
) -> list[str]:
    """Set the thought's tags to ``manual`` (None = keep current manual tags)
    plus the #hashtags found in its content. Returns display names."""
    if manual is None:
        rows = await db.execute(
            select(Tag.name)
            .join(ThoughtTag, ThoughtTag.tag_id == Tag.id)
            .where(ThoughtTag.thought_id == thought.id, ThoughtTag.origin == TagOrigin.manual.value)
        )
        manual = list(rows.scalars())

    origin: dict[str, str] = {}
    names: list[str] = []
    for name in extract_hashtags(thought.content):
        origin[normalize_tag(name)] = TagOrigin.hashtag.value
        names.append(name)
    for name in manual:
        origin[normalize_tag(name)] = TagOrigin.manual.value
        names.append(name)

    tags = await upsert_tags(db, thought.owner_id, names)
    await db.execute(delete(ThoughtTag).where(ThoughtTag.thought_id == thought.id))
    if tags:
        await db.execute(
            insert(ThoughtTag).values(
                [
                    {
                        "thought_id": thought.id,
                        "tag_id": tag.id,
                        "owner_id": thought.owner_id,
                        "origin": origin[key],
                    }
                    for key, tag in tags.items()
                ]
            )
        )
    return [tags[key].name for key in origin if key in tags]


def _usage_subquery(owner_id: uuid.UUID):  # type: ignore[no-untyped-def]
    return (
        select(ThoughtTag.tag_id, func.count().label("usage"))
        .join(Thought, Thought.id == ThoughtTag.thought_id)
        .where(ThoughtTag.owner_id == owner_id, Thought.deleted_at.is_(None))
        .group_by(ThoughtTag.tag_id)
        .subquery()
    )


async def list_tags(
    db: AsyncSession, owner_id: uuid.UUID, prefix: str | None, sort: str, limit: int
) -> list[tuple[Tag, int]]:
    usage = _usage_subquery(owner_id)
    count = func.coalesce(usage.c.usage, 0)
    stmt = (
        select(Tag, count)
        .outerjoin(usage, usage.c.tag_id == Tag.id)
        .where(Tag.owner_id == owner_id)
    )
    if prefix:
        key = normalize_tag(prefix)
        stmt = stmt.where(Tag.normalized_name.like(escape_like(key) + "%", escape="\\"))
    if sort == "popular" or prefix:
        stmt = stmt.order_by(count.desc(), Tag.normalized_name)
    else:
        stmt = stmt.order_by(Tag.normalized_name)
    rows = await db.execute(stmt.limit(limit))
    return [(tag, int(n)) for tag, n in rows.all()]


async def get_tag(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID) -> Tag:
    tag = (
        await db.execute(select(Tag).where(Tag.id == tag_id, Tag.owner_id == owner_id))
    ).scalar_one_or_none()
    if tag is None:
        raise NotFound("Tag not found", code="tag_not_found")
    return tag


async def tag_usage(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(ThoughtTag)
                .join(Thought, Thought.id == ThoughtTag.thought_id)
                .where(
                    ThoughtTag.tag_id == tag_id,
                    ThoughtTag.owner_id == owner_id,
                    Thought.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def create_tag(db: AsyncSession, owner_id: uuid.UUID, name: str) -> Tag:
    key = normalize_tag(name)
    existing = (
        await db.execute(select(Tag).where(Tag.owner_id == owner_id, Tag.normalized_name == key))
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(
            "Tag already exists", code="tag_exists", details={"tag_id": str(existing.id)}
        )
    tag = (await upsert_tags(db, owner_id, [name]))[key]
    await db.commit()
    return tag


async def rename_tag(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID, name: str) -> Tag:
    tag = await get_tag(db, owner_id, tag_id)
    key = normalize_tag(name)
    if not key or len(key) > 64:
        raise ValidationFailed("Tags must be 1-64 characters", code="invalid_tag")
    clash = (
        await db.execute(
            select(Tag.id).where(
                Tag.owner_id == owner_id, Tag.normalized_name == key, Tag.id != tag.id
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise Conflict(
            "Another tag already has this name; merge them instead",
            code="tag_exists",
            details={"tag_id": str(clash)},
        )
    tag.name = display_tag(name)
    tag.normalized_name = key
    await db.flush()
    await _refresh_search_for_tag(db, owner_id, tag.id)
    await db.commit()
    return tag


async def merge_tags(
    db: AsyncSession, owner_id: uuid.UUID, source_id: uuid.UUID, target_id: uuid.UUID
) -> Tag:
    if source_id == target_id:
        raise ValidationFailed("Cannot merge a tag into itself", code="invalid_merge")
    source = await get_tag(db, owner_id, source_id)
    target = await get_tag(db, owner_id, target_id)
    # Move links that the target does not already have, then drop the source.
    await db.execute(
        update(ThoughtTag)
        .where(
            ThoughtTag.tag_id == source.id,
            ~ThoughtTag.thought_id.in_(
                select(ThoughtTag.thought_id).where(ThoughtTag.tag_id == target.id)
            ),
        )
        .values(tag_id=target.id)
    )
    await db.execute(delete(Tag).where(Tag.id == source.id))
    await db.flush()
    await _refresh_search_for_tag(db, owner_id, target.id)
    await db.commit()
    return target


async def delete_tag(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    tag = await get_tag(db, owner_id, tag_id)
    thought_ids = list(
        (
            await db.execute(select(ThoughtTag.thought_id).where(ThoughtTag.tag_id == tag.id))
        ).scalars()
    )
    await db.execute(delete(Tag).where(Tag.id == tag.id))
    await db.flush()
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    await refresh_search_documents(db, owner_id, thought_ids)
    await db.commit()


async def _refresh_search_for_tag(db: AsyncSession, owner_id: uuid.UUID, tag_id: uuid.UUID) -> None:
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    thought_ids = list(
        (
            await db.execute(select(ThoughtTag.thought_id).where(ThoughtTag.tag_id == tag_id))
        ).scalars()
    )
    await refresh_search_documents(db, owner_id, thought_ids)
