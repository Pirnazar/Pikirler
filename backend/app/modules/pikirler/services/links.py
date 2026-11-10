"""Saved copies of the web pages a thought links to.

When a thought contains links, each one gets a ``LinkSnapshot`` and a
background job fetches it once: title, author, date, the article text and a
cover picture. The copy stays with the thought (searchable, exported) even
if the page later changes or disappears. Editing the thought keeps the copies
of links that are still there and drops the others.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageOps
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import NotFound
from app.modules.pikirler.integrations import pages, web
from app.modules.pikirler.models import LinkSnapshot, Thought
from app.modules.pikirler.models.enums import LinkStatus
from app.modules.pikirler.schemas.links import LinkDetail, LinkOut
from app.modules.pikirler.services import jobs
from app.storage import get_storage

log = logging.getLogger("pikirler.links")

FETCH_JOB = "fetch_link"
DELETE_COVERS_JOB = "delete_link_covers"
COVER_MAX_SIDE = 1200
SEARCH_TEXT_CHARS = 50_000

URL_RE = re.compile(r"https?://[^\s<>\"'`(){}\[\]]+", re.IGNORECASE)
_TRAILING = ".,;:!?…»”’'\""


def extract_urls(text: str, limit: int | None = None) -> list[str]:
    """Links in the order they appear, without trailing punctuation, deduplicated."""
    limit = limit or get_settings().link_max_per_thought
    found: list[str] = []
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING)
        if len(url) > 2000 or url in found or len(url) < 12:
            continue
        found.append(url)
        if len(found) >= limit:
            break
    return found


def cover_key(owner_id: uuid.UUID, link_id: uuid.UUID) -> str:
    return f"owners/{owner_id}/links/{link_id}/cover.jpg"


# --- keeping snapshots in step with the thought ------------------------------------


async def sync(db: AsyncSession, thought: Thought) -> None:
    """Called inside the transaction that creates or edits ``thought``."""
    settings = get_settings()
    wanted = extract_urls(thought.content) if settings.link_fetch_enabled else []
    existing = {
        row.url: row
        for row in (
            await db.execute(select(LinkSnapshot).where(LinkSnapshot.thought_id == thought.id))
        ).scalars()
    }
    gone = [row for url, row in existing.items() if url not in wanted]
    if gone:
        keys = [row.cover_key for row in gone if row.cover_key]
        await db.execute(delete(LinkSnapshot).where(LinkSnapshot.id.in_([r.id for r in gone])))
        await _delete_covers_later(db, thought.owner_id, keys)
    for position, url in enumerate(wanted):
        row = existing.get(url)
        if row is not None:
            row.position = position
            continue
        row = LinkSnapshot(
            owner_id=thought.owner_id, thought_id=thought.id, url=url, position=position
        )
        db.add(row)
        await db.flush()
        await _enqueue(db, row)


async def _enqueue(db: AsyncSession, row: LinkSnapshot) -> None:
    await jobs.enqueue(
        db,
        FETCH_JOB,
        {"link_id": str(row.id)},
        owner_id=row.owner_id,
        dedupe_key=f"link:{row.id}",
        max_attempts=4,
    )


async def _delete_covers_later(db: AsyncSession, owner_id: uuid.UUID, keys: list[str]) -> None:
    # Through the outbox: the files go only if the deletion is committed.
    if keys:
        await jobs.enqueue(db, DELETE_COVERS_JOB, {"keys": keys}, owner_id=owner_id)


async def covers_of_thoughts(db: AsyncSession, thought_ids: Sequence[uuid.UUID]) -> list[str]:
    if not thought_ids:
        return []
    rows = await db.execute(
        select(LinkSnapshot.cover_key).where(
            LinkSnapshot.thought_id.in_(list(thought_ids)), LinkSnapshot.cover_key.is_not(None)
        )
    )
    return [k for k in rows.scalars() if k]


async def forget_thoughts(
    db: AsyncSession, owner_id: uuid.UUID, thought_ids: Sequence[uuid.UUID]
) -> None:
    """Before thoughts are hard-deleted: their cover files go with them."""
    await _delete_covers_later(db, owner_id, await covers_of_thoughts(db, thought_ids))


async def refetch(db: AsyncSession, owner_id: uuid.UUID, link_id: uuid.UUID) -> LinkOut:
    row = await _get(db, owner_id, link_id, for_update=True)
    if row.status != LinkStatus.pending.value:
        row.status = LinkStatus.pending.value
        row.error_code = None
        await _enqueue(db, row)
    await db.commit()
    return out(row)


# --- reading ---------------------------------------------------------------------


async def _get(
    db: AsyncSession, owner_id: uuid.UUID, link_id: uuid.UUID, *, for_update: bool = False
) -> LinkSnapshot:
    stmt = (
        select(LinkSnapshot)
        .join(Thought, Thought.id == LinkSnapshot.thought_id)
        .where(
            LinkSnapshot.id == link_id,
            LinkSnapshot.owner_id == owner_id,
            Thought.deleted_at.is_(None),
        )
    )
    if for_update:
        stmt = stmt.with_for_update(of=LinkSnapshot)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise NotFound("Saved page not found", code="link_not_found")
    return row


def _reading_minutes(words: int | None) -> int | None:
    return max(1, round(words / 200)) if words else None


def out(row: LinkSnapshot) -> LinkOut:
    return LinkOut(
        id=row.id,
        url=row.url,
        final_url=row.final_url,
        status=LinkStatus(row.status),
        error_code=row.error_code,
        site_name=row.site_name,
        title=row.title,
        author=row.author,
        published_at=row.published_at,
        excerpt=row.excerpt,
        has_text=bool(row.content_text),
        reading_minutes=_reading_minutes(row.word_count),
        cover_url=f"{get_settings().api_prefix}/links/{row.id}/cover" if row.cover_key else None,
        fetched_at=row.fetched_at,
    )


async def detail(db: AsyncSession, owner_id: uuid.UUID, link_id: uuid.UUID) -> LinkDetail:
    row = await _get(db, owner_id, link_id)
    return LinkDetail(**out(row).model_dump(), text=row.content_text or "")


async def by_thought(
    db: AsyncSession, thought_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[LinkOut]]:
    if not thought_ids:
        return {}
    rows = (
        await db.execute(
            select(LinkSnapshot)
            .where(LinkSnapshot.thought_id.in_(list(thought_ids)))
            .order_by(LinkSnapshot.position)
        )
    ).scalars()
    result: dict[uuid.UUID, list[LinkOut]] = {}
    for row in rows:
        result.setdefault(row.thought_id, []).append(out(row))
    return result


async def search_texts(db: AsyncSession, thought_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Title, site, author and text of saved pages, for the search document."""
    if not thought_ids:
        return {}
    rows = await db.execute(
        select(
            LinkSnapshot.thought_id,
            LinkSnapshot.site_name,
            LinkSnapshot.title,
            LinkSnapshot.author,
            LinkSnapshot.content_text,
        ).where(
            LinkSnapshot.thought_id.in_(list(thought_ids)),
            LinkSnapshot.status == LinkStatus.ready.value,
        )
    )
    texts: dict[uuid.UUID, list[str]] = {}
    for tid, site, title, author, text in rows.all():
        parts = [p for p in (site, title, author, (text or "")[:SEARCH_TEXT_CHARS]) if p]
        texts.setdefault(tid, []).append("\n".join(parts))
    return {tid: "\n".join(v) for tid, v in texts.items()}


async def cover_bytes(db: AsyncSession, owner_id: uuid.UUID, link_id: uuid.UUID) -> bytes:
    row = await _get(db, owner_id, link_id)
    if not row.cover_key:
        raise NotFound("No cover", code="link_cover_not_found")
    return await get_storage().read_prefix(row.cover_key, get_settings().link_max_image_bytes)


# --- the job ---------------------------------------------------------------------


def _jpeg(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as raw:
        img: Image.Image = ImageOps.exif_transpose(raw)
        img = img.convert("RGB")
        img.thumbnail((COVER_MAX_SIDE, COVER_MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)  # also drops all metadata
        return buf.getvalue()


async def _store_cover(owner_id: uuid.UUID, link_id: uuid.UUID, image_url: str) -> str | None:
    """Download and re-encode the page's picture. Any failure just means no cover."""
    try:
        got = await web.fetch(
            image_url,
            kinds=("image/",),
            max_bytes=get_settings().link_max_image_bytes,
            accept="image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
        )
        jpeg = await asyncio.to_thread(_jpeg, got.body)
    except Exception as exc:  # a missing cover is fine
        log.info("link_cover_skipped", extra={"error_type": type(exc).__name__})
        return None
    key = cover_key(owner_id, link_id)
    await get_storage().put_file(key, io.BytesIO(jpeg), "image/jpeg")
    return key


async def run(db: AsyncSession, link_id: uuid.UUID) -> dict[str, Any]:
    row = await db.get(LinkSnapshot, link_id)
    if row is None:
        return {"skipped": "gone"}
    if row.status != LinkStatus.pending.value:
        return {"skipped": row.status}
    url, owner_id, thought_id = row.url, row.owner_id, row.thought_id
    await db.commit()  # no transaction stays open during the download

    try:
        page = await pages.extract(url)
    except web.FetchError as exc:
        if exc.retryable:
            raise  # the runner retries with backoff; ``gave_up`` marks it failed
        return await _fail(db, link_id, exc.code)
    cover = await _store_cover(owner_id, link_id, page.image_url) if page.image_url else None

    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is None or row.url != url:  # the link was removed meanwhile
        await _delete_covers_later(db, owner_id, [cover] if cover else [])
        await db.commit()
        return {"skipped": "gone"}
    old_cover = row.cover_key
    row.final_url = page.final_url if page.final_url != url else None
    row.site_name = page.site_name
    row.title = page.title
    row.author = page.author
    row.published_at = page.published_at
    row.excerpt = page.excerpt
    row.content_text = page.text or None
    row.word_count = len(page.text.split()) if page.text else None
    row.cover_key = cover or old_cover
    row.status = LinkStatus.ready.value
    row.error_code = None
    row.fetched_at = utcnow()
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    await refresh_search_documents(db, owner_id, [thought_id])
    await db.commit()
    log.info("link_saved", extra={"words": row.word_count or 0})
    return {"status": "ready", "words": row.word_count or 0}


async def _fail(db: AsyncSession, link_id: uuid.UUID, code: str) -> dict[str, Any]:
    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is not None:
        row.status = LinkStatus.failed.value
        row.error_code = code[:64]
        await db.commit()
    log.info("link_failed", extra={"error_code": code})
    return {"status": "failed", "error_code": code}


async def run_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await run(db, uuid.UUID(payload["link_id"]))


async def gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    await _fail(db, uuid.UUID(payload["link_id"]), web.Unreachable.code)


async def delete_covers_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    storage = get_storage()
    for key in payload.get("keys", []):
        await storage.delete(key)
    return {"deleted": len(payload.get("keys", []))}
