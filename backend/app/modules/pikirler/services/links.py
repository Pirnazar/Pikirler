"""Saved copies of the web pages a thought links to.

When a thought contains links, each one gets a ``LinkSnapshot`` and a
background job fetches it once: title, author, date, the article text and a
cover picture. The copy stays with the thought (searchable, exported) even
if the page later changes or disappears. Editing the thought keeps the copies
of links that are still there and drops the others.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from PIL import Image, ImageOps
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import NotFound
from app.modules.pikirler.integrations import media_download, pages, web
from app.modules.pikirler.models import Attachment, LinkSnapshot, Thought
from app.modules.pikirler.models.enums import AttachmentKind, LinkStatus, ProcessingStatus
from app.modules.pikirler.schemas.links import LinkDetail, LinkMedia, LinkOut, SavedComment
from app.modules.pikirler.services import jobs, media
from app.storage import get_storage

log = logging.getLogger("pikirler.links")

FETCH_JOB = "fetch_link"
MEDIA_JOB = "download_link_media"
MEDIA_SITES = {"YouTube", "TikTok", "Instagram"}
SYNC_JOB = "sync_link"
SYNC_BATCH = 20
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


POST_TEXT_CHARS = 1200


def _profile_url(row_site: str | None, username: str | None) -> str | None:
    if not username:
        return None
    handle = quote(username, safe="")
    profiles = {
        "X": f"https://x.com/{handle}",
        "YouTube": f"https://www.youtube.com/@{handle}",
        "TikTok": f"https://www.tiktok.com/@{handle}",
        "Instagram": f"https://www.instagram.com/{handle}/",
    }
    if row_site in profiles:
        return profiles[row_site]
    if row_site != "Teswirler":
        return None
    site = get_settings().teswirler_site_url.rstrip("/")
    return f"{site}/@{quote(username, safe='')}"


def _comment(raw: dict[str, Any], site: str | None) -> SavedComment:
    return SavedComment(
        id=raw.get("id"),
        author=raw.get("author"),
        username=raw.get("username"),
        author_url=_profile_url(site, raw.get("username")),
        text=raw.get("text") or "",
        quote=raw.get("quote"),
        created_at=raw.get("created_at"),
        likes=raw.get("likes") or 0,
        image_url=raw.get("image_url"),
        deleted_at=raw.get("deleted_at"),
        replies=[_comment(r, site) for r in raw.get("replies") or [] if isinstance(r, dict)],
    )


def _post_text(row: LinkSnapshot) -> str | None:
    if row.kind != "post" or not row.content_text:
        return None
    text = row.content_text
    if len(text) <= POST_TEXT_CHARS:
        return text
    return text[: POST_TEXT_CHARS - 1].rsplit(" ", 1)[0] + "…"


def _media(att: Attachment | None) -> LinkMedia | None:
    if att is None or att.deleted_at is not None:
        return None
    return LinkMedia(
        id=att.id,
        mime_type=att.mime_type,
        file_size=att.file_size,
        processing_status=att.processing_status,
        width=att.width,
        height=att.height,
        duration_seconds=att.duration_seconds,
        content_url=_media_url(att.id),
        thumbnail_url=_media_url(att.id, "thumbnail") if att.thumbnail_key else None,
    )


def _media_url(attachment_id: uuid.UUID, variant: str = "content") -> str:
    return f"{get_settings().api_prefix}/media/{attachment_id}/{variant}"


async def _media_of(db: AsyncSession, rows: Sequence[LinkSnapshot]) -> dict[uuid.UUID, Attachment]:
    ids = [r.media_attachment_id for r in rows if r.media_attachment_id]
    if not ids:
        return {}
    found = (await db.execute(select(Attachment).where(Attachment.id.in_(ids)))).scalars()
    return {a.id: a for a in found}


def out(row: LinkSnapshot, media_att: Attachment | None = None) -> LinkOut:
    return LinkOut(
        id=row.id,
        url=row.url,
        final_url=row.final_url,
        status=LinkStatus(row.status),
        error_code=row.error_code,
        kind="post" if row.kind == "post" else "article",
        site_name=row.site_name,
        title=row.title,
        author=row.author,
        author_username=row.author_username,
        author_url=_profile_url(row.site_name, row.author_username),
        published_at=row.published_at,
        edited_at=row.edited_at,
        comment_count=row.comment_count,
        deleted_comment_count=row.deleted_comment_count or 0,
        synced_at=row.synced_at,
        source_deleted_at=row.source_deleted_at,
        post_text=_post_text(row),
        excerpt=row.excerpt,
        has_text=bool(row.content_text),
        reading_minutes=None if row.duration_seconds else _reading_minutes(row.word_count),
        duration_seconds=row.duration_seconds,
        media_status=row.media_status
        if row.media_status in ("pending", "ready", "failed")
        else None,
        media_error=row.media_error,
        media=_media(media_att),
        cover_url=f"{get_settings().api_prefix}/links/{row.id}/cover" if row.cover_key else None,
        fetched_at=row.fetched_at,
    )


async def detail(db: AsyncSession, owner_id: uuid.UUID, link_id: uuid.UUID) -> LinkDetail:
    row = await _get(db, owner_id, link_id)
    comments = [_comment(c, row.site_name) for c in row.comments or [] if isinstance(c, dict)]
    att = (
        (await _media_of(db, [row])).get(row.media_attachment_id)
        if row.media_attachment_id
        else None
    )
    return LinkDetail(**out(row, att).model_dump(), text=row.content_text or "", comments=comments)


async def by_thought(
    db: AsyncSession, thought_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[LinkOut]]:
    if not thought_ids:
        return {}
    rows = list(
        (
            await db.execute(
                select(LinkSnapshot)
                .where(LinkSnapshot.thought_id.in_(list(thought_ids)))
                .order_by(LinkSnapshot.position)
            )
        ).scalars()
    )
    atts = await _media_of(db, rows)
    result: dict[uuid.UUID, list[LinkOut]] = {}
    for row in rows:
        att = atts.get(row.media_attachment_id) if row.media_attachment_id else None
        result.setdefault(row.thought_id, []).append(out(row, att))
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
            LinkSnapshot.author_username,
            LinkSnapshot.comments,
        ).where(
            LinkSnapshot.thought_id.in_(list(thought_ids)),
            LinkSnapshot.status == LinkStatus.ready.value,
        )
    )
    texts: dict[uuid.UUID, list[str]] = {}
    for tid, site, title, author, text, username, comments in rows.all():
        said = "\n".join(_comment_lines(comments or []))[:SEARCH_TEXT_CHARS]
        parts = [site, title, author, username, (text or "")[:SEARCH_TEXT_CHARS], said]
        texts.setdefault(tid, []).append("\n".join(p for p in parts if p))
    return {tid: "\n".join(v) for tid, v in texts.items()}


def _comment_lines(comments: list[Any]) -> list[str]:
    lines: list[str] = []
    for c in comments:
        if isinstance(c, dict):
            lines.append(" ".join(str(c.get(k) or "") for k in ("author", "username", "text")))
            lines.extend(_comment_lines(c.get("replies") or []))
    return lines


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


# --- comments kept in step with the site ----------------------------------------


def _mark_deleted(comment: dict[str, Any], when: str) -> dict[str, Any]:
    marked = dict(comment)
    marked["deleted_at"] = comment.get("deleted_at") or when
    marked["replies"] = [
        _mark_deleted(r, when) for r in comment.get("replies") or [] if isinstance(r, dict)
    ]
    return marked


def merge_comments(
    old: list[Any], new: list[dict[str, Any]], complete: bool, when: str
) -> list[dict[str, Any]]:
    """The site's current comments plus every comment saved before that is no
    longer there. Those are marked deleted, but only when ``new`` is the
    complete list: a partial download never marks anything."""
    old_by_id = {c["id"]: c for c in old if isinstance(c, dict) and c.get("id")}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for comment in new:
        before = old_by_id.get(comment.get("id") or "")
        item = dict(comment)
        item["deleted_at"] = None
        item["replies"] = merge_comments(
            (before or {}).get("replies") or [], comment.get("replies") or [], complete, when
        )
        if comment.get("id"):
            seen.add(comment["id"])
        merged.append(item)
    for comment in old:
        if not isinstance(comment, dict) or (comment.get("id") and comment["id"] in seen):
            continue
        merged.append(_mark_deleted(comment, when) if complete and comment.get("id") else comment)
    merged.sort(key=lambda c: c.get("created_at") or "")
    return merged


def count_comments(comments: list[Any]) -> tuple[int, int]:
    """(still on the site, deleted on the site)."""
    alive = deleted = 0
    for c in comments:
        if not isinstance(c, dict):
            continue
        if c.get("deleted_at"):
            deleted += 1
        else:
            alive += 1
        a, d = count_comments(c.get("replies") or [])
        alive, deleted = alive + a, deleted + d
    return alive, deleted


def _apply(row: LinkSnapshot, page: pages.Page) -> None:
    """Copy what was read from the site onto the snapshot."""
    now = utcnow()
    row.final_url = page.final_url if page.final_url != row.url else None
    row.kind = page.kind
    row.site_name = page.site_name
    row.title = page.title
    row.author = page.author
    row.author_username = page.author_username
    row.published_at = page.published_at
    row.edited_at = page.edited_at
    if page.comments is not None:
        # A failed comment download keeps what was saved before.
        row.comments = merge_comments(
            row.comments or [], page.comments, page.comments_complete, now.isoformat()
        )
        row.comment_count, row.deleted_comment_count = count_comments(row.comments)
    row.excerpt = page.excerpt
    row.content_text = page.text or None
    row.word_count = len(page.text.split()) if page.text else None
    row.duration_seconds = page.duration_seconds
    row.status = LinkStatus.ready.value
    row.error_code = None
    row.synced_at = now
    row.source_deleted_at = None


async def _source_gone(db: AsyncSession, link_id: uuid.UUID) -> dict[str, Any]:
    """The original no longer exists: keep the copy, note when it went."""
    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is not None:
        now = utcnow()
        row.source_deleted_at = row.source_deleted_at or now
        row.synced_at = now
        row.status = LinkStatus.ready.value
        row.error_code = None
        await db.commit()
    log.info("link_source_deleted")
    return {"status": "source_deleted"}


async def run(db: AsyncSession, link_id: uuid.UUID) -> dict[str, Any]:
    """First download of a link, or a requested refresh."""
    row = await db.get(LinkSnapshot, link_id)
    if row is None:
        return {"skipped": "gone"}
    if row.status != LinkStatus.pending.value:
        return {"skipped": row.status}
    url, owner_id, thought_id = row.url, row.owner_id, row.thought_id
    had_copy = row.fetched_at is not None
    await db.commit()  # no transaction stays open during the download

    try:
        page = await pages.extract(url)
    except web.PageNotFound:
        if had_copy:
            return await _source_gone(db, link_id)
        return await _fail(db, link_id, web.PageNotFound.code)
    except web.FetchError as exc:
        if exc.retryable:
            raise  # the runner retries with backoff; ``gave_up`` ends it
        return await _fail(db, link_id, exc.code)
    cover = await _store_cover(owner_id, link_id, page.image_url) if page.image_url else None

    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is None or row.url != url:  # the link was removed meanwhile
        await _delete_covers_later(db, owner_id, [cover] if cover else [])
        await db.commit()
        return {"skipped": "gone"}
    _apply(row, page)
    row.cover_key = cover or row.cover_key
    row.fetched_at = utcnow()
    await _queue_media(db, row, retry_failed=True)
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    await refresh_search_documents(db, owner_id, [thought_id])
    await db.commit()
    await jobs.kick(db)
    log.info("link_saved", extra={"words": row.word_count or 0})
    return {"status": "ready", "words": row.word_count or 0}


async def sync_with_site(db: AsyncSession, link_id: uuid.UUID) -> dict[str, Any]:
    """Background check of a saved copy against the site. Never shows the
    copy as "saving" and never fails it: a problem just waits for next time."""
    row = await db.get(LinkSnapshot, link_id)
    if row is None or row.status != LinkStatus.ready.value:
        return {"skipped": "not ready"}
    url, owner_id, thought_id = row.url, row.owner_id, row.thought_id
    needs_cover = row.cover_key is None
    await db.commit()

    try:
        page = await pages.extract(url)
    except web.PageNotFound:
        return await _source_gone(db, link_id)
    except web.FetchError as exc:
        row = await db.get(LinkSnapshot, link_id, with_for_update=True)
        if row is not None:
            row.synced_at = utcnow()
            await db.commit()
        return {"status": "unchanged", "error_code": exc.code}
    cover = None
    if needs_cover and page.image_url:
        cover = await _store_cover(owner_id, link_id, page.image_url)

    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is None or row.url != url or row.status != LinkStatus.ready.value:
        await _delete_covers_later(db, owner_id, [cover] if cover else [])
        await db.commit()
        return {"skipped": "changed"}
    _apply(row, page)
    row.cover_key = cover or row.cover_key
    await _queue_media(db, row, retry_failed=False)
    from app.modules.pikirler.services.thoughts import refresh_search_documents

    await refresh_search_documents(db, owner_id, [thought_id])
    await db.commit()
    await jobs.kick(db)
    log.info("link_synced", extra={"count": row.comment_count or 0})
    return {"status": "synced", "comments": row.comment_count, "deleted": row.deleted_comment_count}


def sync_interval(age: timedelta) -> timedelta:
    """How often a copy is checked, by how old the original is."""
    if age < timedelta(days=2):
        return timedelta(minutes=30)
    if age < timedelta(days=14):
        return timedelta(hours=6)
    if age < timedelta(days=90):
        return timedelta(days=1)
    return timedelta(days=7)


# Sites whose copies are checked in the background. Teswirler (and YouTube with an
# API key) also bring new and deleted comments; the others: edits and deletion.
SYNCABLE = (
    LinkSnapshot.url.ilike("https://teswirler.com/%")
    | LinkSnapshot.url.ilike("https://www.teswirler.com/%")
    | LinkSnapshot.site_name.in_(["X", "YouTube", "TikTok", "Instagram"])
)


async def schedule_sync(db: AsyncSession, batch: int = SYNC_BATCH) -> int:
    """Queue background checks for the copies that are due (worker cron)."""
    if not get_settings().link_sync_enabled:
        return 0
    now = utcnow()
    last = func.coalesce(LinkSnapshot.synced_at, LinkSnapshot.fetched_at)
    rows = (
        await db.execute(
            select(LinkSnapshot.id, LinkSnapshot.owner_id, LinkSnapshot.published_at, last)
            .join(Thought, Thought.id == LinkSnapshot.thought_id)
            .where(
                LinkSnapshot.status == LinkStatus.ready.value,
                Thought.deleted_at.is_(None),
                SYNCABLE,
            )
            .order_by(last.asc().nulls_first())
            .limit(500)
        )
    ).all()
    due = []
    for link_id, owner_id, published, checked in rows:
        age = now - (published or checked or now)
        if checked is None or now - checked >= sync_interval(age):
            due.append((link_id, owner_id))
        if len(due) >= batch:
            break
    for link_id, owner_id in due:
        await jobs.enqueue(
            db,
            SYNC_JOB,
            {"link_id": str(link_id)},
            owner_id=owner_id,
            dedupe_key=f"link-sync:{link_id}",
            max_attempts=1,
        )
    await db.commit()
    await jobs.kick(db)
    return len(due)


async def _fail(db: AsyncSession, link_id: uuid.UUID, code: str) -> dict[str, Any]:
    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is not None:
        # A copy that exists stays readable; only a first download can fail.
        row.status = LinkStatus.ready.value if row.fetched_at else LinkStatus.failed.value
        row.error_code = None if row.fetched_at else code[:64]
        await db.commit()
    log.info("link_failed", extra={"error_code": code})
    return {"status": "failed", "error_code": code}


async def run_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await run(db, uuid.UUID(payload["link_id"]))


async def sync_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await sync_with_site(db, uuid.UUID(payload["link_id"]))


async def gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    await _fail(db, uuid.UUID(payload["link_id"]), web.Unreachable.code)


# --- the video itself (YouTube, TikTok, Instagram) -------------------------------------


async def _queue_media(db: AsyncSession, row: LinkSnapshot, *, retry_failed: bool) -> None:
    if row.site_name not in MEDIA_SITES or get_settings().link_media_max_mb <= 0:
        return
    if row.media_attachment_id is not None or row.media_status == "pending":
        return
    if row.media_status == "failed" and not retry_failed:
        return
    row.media_status = "pending"
    row.media_error = None
    await jobs.enqueue(
        db,
        MEDIA_JOB,
        {"link_id": str(row.id)},
        owner_id=row.owner_id,
        dedupe_key=f"link-media:{row.id}",
        max_attempts=2,
    )


async def _media_failed(db: AsyncSession, link_id: uuid.UUID, code: str) -> dict[str, Any]:
    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is not None and row.media_status == "pending":
        row.media_status = "failed"
        row.media_error = code[:64]
        await db.commit()
    log.info("link_media_failed", extra={"error_code": code})
    return {"status": "failed", "error_code": code}


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def download_media(db: AsyncSession, link_id: uuid.UUID) -> dict[str, Any]:
    row = await db.get(LinkSnapshot, link_id)
    if row is None or row.media_status != "pending":
        return {"skipped": "not pending"}
    url, owner_id = row.final_url or row.url, row.owner_id
    await db.commit()

    attachment_id = uuid.uuid4()
    key = media.storage_key(owner_id, attachment_id, "original")
    with tempfile.TemporaryDirectory(prefix="pikirler-media-") as workdir:
        try:
            got = await asyncio.to_thread(media_download.download, url, workdir)
        except media_download.MediaError as exc:
            return await _media_failed(db, link_id, exc.code)
        kind = media.ALLOWED.get(got.mime_type)
        size = os.path.getsize(got.path)
        if kind is not AttachmentKind.video:
            return await _media_failed(db, link_id, "media_failed")
        if size > media.max_bytes_for(kind):
            return await _media_failed(db, link_id, "media_too_large")
        checksum = await asyncio.to_thread(_sha256, got.path)
        fh = await asyncio.to_thread(open, got.path, "rb")
        try:
            await get_storage().put_file(key, fh, got.mime_type)
        finally:
            fh.close()

    row = await db.get(LinkSnapshot, link_id, with_for_update=True)
    if row is None or row.media_status != "pending":  # removed meanwhile
        await get_storage().delete(key)
        return {"skipped": "gone"}
    now = utcnow()
    db.add(
        Attachment(
            id=attachment_id,
            owner_id=owner_id,
            storage_key=key,
            original_filename=got.filename[:255],
            safe_filename=media.safe_filename(got.filename),
            mime_type=got.mime_type,
            declared_mime_type=got.mime_type,
            kind=AttachmentKind.video.value,
            file_size=size,
            checksum=checksum,
            processing_status=ProcessingStatus.uploaded.value,
            uploaded_at=now,
        )
    )
    await db.flush()
    row.media_attachment_id = attachment_id
    row.media_status = "ready"
    row.media_error = None
    # Thumbnail, duration and dimensions, like any uploaded video.
    await jobs.enqueue(
        db,
        media.PROCESS_JOB,
        {"attachment_id": str(attachment_id)},
        owner_id=owner_id,
        dedupe_key=f"process:{attachment_id}",
    )
    await db.commit()
    await jobs.kick(db)
    log.info("link_media_saved", extra={"bytes": size})
    return {"status": "ready", "bytes": size}


async def media_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await download_media(db, uuid.UUID(payload["link_id"]))


async def media_gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    await _media_failed(db, uuid.UUID(payload["link_id"]), "media_failed")


async def delete_covers_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    storage = get_storage()
    for key in payload.get("keys", []):
        await storage.delete(key)
    return {"deleted": len(payload.get("keys", []))}
