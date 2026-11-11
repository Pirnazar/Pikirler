"""Incremental owner-scoped corpus built from Cinema and the immutable YouTube snapshot."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path

import httpx
from sqlalchemy import case, delete, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.youtube_archive import archive
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import ProviderUnavailable
from app.core.text import build_search_document
from app.modules.pikirler.models.cinema import CinemaCue, CinemaMovie, CinemaSubtitle
from app.modules.pikirler.models.media_search import MediaEntry, MediaIndexState, MediaPassage
from app.modules.pikirler.services import jobs
from app.modules.pikirler.services.cinema import language_code

SYNC_JOB = "media_corpus_sync"
EMBED_JOB = "media_corpus_embed"
NAMESPACE = uuid.UUID("11380744-13c2-4d06-8ca2-339677dff790")
CATEGORIES = {
    "2": ["Автомобили", "Autos & Vehicles"],
    "15": ["Животные", "Pets & Animals"],
    "19": ["Путешествия", "Travel & Events"],
    "29": ["Общество", "Nonprofits & Activism"],
    "1": ["Кино и анимация", "Film & Animation"],
    "10": ["Музыка", "Music"],
    "17": ["Спорт", "Sports"],
    "20": ["Игры", "Gaming"],
    "22": ["Блоги", "People & Blogs"],
    "23": ["Комедия", "Comedy"],
    "24": ["Развлечения", "Entertainment"],
    "25": ["Новости", "News & Politics"],
    "26": ["Советы", "Howto & Style"],
    "27": ["Образование", "Education"],
    "28": ["Наука и технологии", "Science & Technology"],
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def inferred_language(value: str | None, body: str = "") -> str:
    if value and value not in {"unknown", "und"}:
        return language_code(value)
    letters = [c for c in body if c.isalpha()]
    if letters and sum("\u0400" <= c <= "\u04ff" for c in letters) / len(letters) > 0.6:
        return "ru"
    if any(c in body.casefold() for c in "ýňä"):
        return "tk"
    return "und"


def chunks(lines: list[dict], limit: int = 1400) -> list[dict]:
    """Keep adjacent lines and timestamps together, with one line of context overlap."""
    result, current, size = [], [], 0
    for line in lines:
        body = str(line.get("text") or "").strip()
        if not body:
            continue
        # Provider-added promotions are not film dialogue or lyrics.
        if re.search(r"\b(?:tryray\.app|www\.opensubtitles\.(?:org|com))\b", body, re.I):
            continue
        clean = {**line, "text": body}
        if current and size + len(body) > limit:
            result.append(_chunk(current))
            current = current[-1:] if len(current) > 1 else []
            size = sum(len(item["text"]) for item in current)
        current.append(clean)
        size += len(body) + 1
    if current:
        result.append(_chunk(current))
    return result


def _chunk(lines: list[dict]) -> dict:
    timed = [line for line in lines if line.get("start_ms") is not None]
    return {
        "body": "\n".join(line["text"] for line in lines),
        "segments": lines,
        "start_ms": timed[0]["start_ms"] if timed else None,
        "end_ms": timed[-1].get("end_ms") if timed else None,
    }


async def index_state(db: AsyncSession, owner_id: uuid.UUID) -> MediaIndexState:
    await db.execute(insert(MediaIndexState).values(owner_id=owner_id).on_conflict_do_nothing())
    return await db.get(MediaIndexState, owner_id)


async def queue_sync(db: AsyncSession, owner_id: uuid.UUID) -> None:
    await jobs.enqueue(
        db,
        SYNC_JOB,
        {"owner_id": str(owner_id)},
        owner_id=owner_id,
        dedupe_key=f"media-sync:{owner_id}",
        max_attempts=3,
    )


async def schedule(db: AsyncSession) -> int:
    owners = set((await db.execute(select(CinemaMovie.owner_id).distinct())).scalars())
    configured = get_settings().youtube_archive_owner_id
    if configured:
        owners.add(uuid.UUID(configured))
    for owner_id in owners:
        await queue_sync(db, owner_id)
    await db.commit()
    await jobs.kick(db)
    return len(owners)


async def put_entry(
    db: AsyncSession, owner_id: uuid.UUID, data: dict, passages: list[dict]
) -> bool:
    key = data["source_key"]
    entry_id = uuid.uuid5(NAMESPACE, f"{owner_id}:{key}")
    old = (
        await db.execute(
            select(MediaEntry.signature, MediaEntry.title, MediaEntry.title_en).where(
                MediaEntry.id == entry_id
            )
        )
    ).first()
    if old and old.signature == data["signature"]:
        return False
    context_changed = bool(
        old and (old.title != data["title"] or old.title_en != data.get("title_en"))
    )
    values = {"id": entry_id, "owner_id": owner_id, **data, "updated_at": utcnow()}
    await db.execute(
        insert(MediaEntry)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["owner_id", "source_key"],
            set_={k: v for k, v in values.items() if k not in {"id", "owner_id", "source_key"}},
        )
    )
    ids, rows = [], []
    for passage in passages:
        pid = uuid.uuid5(entry_id, passage["source_key"])
        ids.append(pid)
        rows.append(
            {
                "id": pid,
                "owner_id": owner_id,
                "entry_id": entry_id,
                **passage,
                "search_document": build_search_document(passage["body"]),
                "content_hash": digest(passage),
                "embedding": None,
                "embedding_model": None,
            }
        )
    for start in range(0, len(rows), 150):
        stmt = insert(MediaPassage).values(rows[start : start + 150])
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=["entry_id", "source_key"],
                set_={
                    key: getattr(stmt.excluded, key)
                    for key in rows[start]
                    if key not in {"id", "owner_id", "entry_id", "source_key"}
                },
                where=or_(
                    MediaPassage.content_hash != stmt.excluded.content_hash,
                    literal(context_changed),
                ),
            )
        )
    obsolete = delete(MediaPassage).where(MediaPassage.entry_id == entry_id)
    if ids:
        obsolete = obsolete.where(MediaPassage.id.not_in(ids))
    await db.execute(obsolete)
    return True


def metadata_passage(body: str, language: str) -> dict:
    return {
        "source_key": "metadata:" + language,
        "language": language,
        "part": "metadata",
        "body": body,
        "segments": [],
        "start_ms": None,
        "end_ms": None,
    }


async def sync_movie(db: AsyncSession, movie: CinemaMovie) -> bool:
    ru = movie.localized.get("ru") or {}
    tracks = list(
        (
            await db.execute(select(CinemaSubtitle).where(CinemaSubtitle.movie_id == movie.id))
        ).scalars()
    )
    genres = list(dict.fromkeys([*ru.get("genres", []), *movie.genres]))
    data = {
        "source_key": f"movie:{movie.id}",
        "movie_id": movie.id,
        "kind": "movie",
        "title": ru.get("title") or movie.title,
        "title_en": movie.title,
        "creator": ", ".join(ru.get("director") or movie.director),
        "year": int(movie.year[:4]) if movie.year and movie.year[:4].isdigit() else None,
        "genres": genres,
        "poster": movie.poster,
    }
    data["signature"] = digest(
        [data, movie.localized, movie.description, [(str(t.id), t.cue_count) for t in tracks]]
    )
    entry_id = uuid.uuid5(NAMESPACE, f"{movie.owner_id}:{data['source_key']}")
    old = await db.scalar(select(MediaEntry.signature).where(MediaEntry.id == entry_id))
    if old == data["signature"]:
        return False
    passages = [
        metadata_passage(
            "\n".join(
                [
                    movie.title,
                    movie.description or "",
                    ", ".join(movie.genres),
                    ", ".join(movie.director),
                    ", ".join(movie.cast),
                ]
            ),
            "en",
        )
    ]
    if ru:
        passages.append(
            metadata_passage(
                "\n".join(
                    [
                        ru.get("title") or movie.title,
                        ru.get("description") or "",
                        ", ".join(ru.get("genres") or []),
                        ", ".join(ru.get("director") or []),
                        ", ".join(ru.get("cast") or []),
                    ]
                ),
                "ru",
            )
        )
    for track in tracks:
        cues = (
            await db.execute(
                select(CinemaCue)
                .where(CinemaCue.subtitle_id == track.id)
                .order_by(CinemaCue.position)
            )
        ).scalars()
        lines = [
            {
                "text": cue.body,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "reference": str(cue.id),
                "track_id": str(track.id),
            }
            for cue in cues
        ]
        for i, chunk in enumerate(chunks(lines)):
            passages.append(
                {
                    **chunk,
                    "source_key": f"cinema:{track.id}:{i}",
                    "language": language_code(track.language),
                    "part": "subtitle",
                }
            )
    return await put_entry(db, movie.owner_id, data, passages)


def youtube_rows(connection: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute("""
        SELECT v.*, t.id AS transcript_id, t.status AS transcript_status,
               t.language_code AS transcript_language, t.full_text AS transcript_text,
               l.id AS lyrics_id, l.status AS lyrics_status, l.language_code AS lyrics_language,
               l.full_text AS lyrics_text
        FROM youtube_videos v
        LEFT JOIN youtube_transcripts t ON t.video_id = v.id
        LEFT JOIN song_lyrics l ON l.video_id = v.id
        ORDER BY v.id
    """)
    ]


def youtube_passages(connection: sqlite3.Connection, video: dict) -> list[dict]:
    passages = [
        metadata_passage(
            "\n".join(
                str(video.get(k) or "")
                for k in ("title", "channel_title", "artist", "track_title", "description")
            ),
            inferred_language(None, video.get("description") or video.get("title") or ""),
        )
    ]
    for prefix, position in (
        ("transcript", "segment_index"),
        ("lyrics", "line_index"),
    ):
        if video.get(prefix + "_status") != "ok":
            continue
        body = video.get(prefix + "_text") or ""
        language = inferred_language(video.get(prefix + "_language"), body)
        sql = (
            "SELECT * FROM youtube_transcript_segments WHERE transcript_id=? ORDER BY segment_index"
            if prefix == "transcript"
            else "SELECT * FROM song_lyric_lines WHERE lyrics_id=? ORDER BY line_index"
        )
        rows = connection.execute(sql, (video[prefix + "_id"],))
        lines = []
        for row in rows:
            row = dict(row)
            start = row.get("start_seconds")
            start_ms = max(0, int(float(start) * 1000)) if start is not None else None
            duration = row.get("duration_seconds")
            end_ms = (
                start_ms + max(0, int(float(duration) * 1000))
                if start_ms is not None and duration is not None
                else start_ms
            )
            lines.append(
                {
                    "text": row["text"],
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "reference": str(row[position]),
                    "video_id": video["youtube_video_id"],
                }
            )
        if not lines and body:
            lines = [
                {
                    "text": line,
                    "start_ms": None,
                    "end_ms": None,
                    "reference": str(i),
                    "video_id": video["youtube_video_id"],
                }
                for i, line in enumerate(body.splitlines())
            ]
        for i, chunk in enumerate(chunks(lines)):
            passages.append(
                {
                    **chunk,
                    "source_key": f"{prefix}:{i}",
                    "language": language,
                    "part": "lyrics" if prefix == "lyrics" else "subtitle",
                }
            )
    return passages


async def sync_corpus(db: AsyncSession, payload: dict) -> dict:
    owner_id = uuid.UUID(payload["owner_id"])
    state = await index_state(db, owner_id)
    state.phase, state.error, state.processed_items = "syncing", None, 0
    await db.commit()
    movies = list(
        (await db.execute(select(CinemaMovie).where(CinemaMovie.owner_id == owner_id))).scalars()
    )
    changed = 0
    for movie in movies:
        changed += await sync_movie(db, movie)
        await db.commit()
    if get_settings().youtube_archive_owner_id == str(owner_id):
        import os

        path = Path(os.getenv("PIKIRLER_YOUTUBE_ARCHIVE", "/srv/youtube-archive/youtube.sqlite3"))
        stamp = f"{path.stat().st_mtime_ns}:{path.stat().st_size}" if path.exists() else None
        if stamp and stamp != state.archive_stamp:
            # One connection retains a consistent snapshot if export replaces the file.
            with archive() as connection:
                if connection:
                    rows = youtube_rows(connection)
                    state.total_items = len(rows) + len(movies)
                    await db.commit()
                    for i, video in enumerate(rows):
                        data = {
                            "source_key": "youtube:" + video["youtube_video_id"],
                            "movie_id": None,
                            "kind": video.get("content_kind")
                            if video.get("content_kind") in {"music", "video"}
                            else "unknown",
                            "title": video.get("title") or video["youtube_video_id"],
                            "title_en": None,
                            "creator": video.get("artist") or video.get("channel_title"),
                            "year": int(str(video["published_at"])[:4])
                            if video.get("published_at")
                            and str(video["published_at"])[:4].isdigit()
                            else None,
                            "genres": CATEGORIES.get(str(video.get("category_id")), []),
                            "poster": video.get("thumbnail_url"),
                        }
                        data["signature"] = digest(
                            [
                                data,
                                video.get("description"),
                                video.get("transcript_text"),
                                video.get("lyrics_text"),
                                video.get("transcript_language"),
                                video.get("lyrics_language"),
                            ]
                        )
                        eid = uuid.uuid5(NAMESPACE, f"{owner_id}:{data['source_key']}")
                        old = await db.scalar(
                            select(MediaEntry.signature).where(MediaEntry.id == eid)
                        )
                        if old != data["signature"]:
                            passages = youtube_passages(connection, video)
                            changed += await put_entry(db, owner_id, data, passages)
                        if i % 20 == 0:
                            state.processed_items = len(movies) + i + 1
                            await db.commit()
                            await asyncio.sleep(0)
                    state.archive_stamp = stamp
    state.processed_items = state.total_items
    state.last_sync_at, state.phase = utcnow(), "indexing"
    if get_settings().media_encoder_url:
        await jobs.enqueue(
            db,
            EMBED_JOB,
            {"owner_id": str(owner_id)},
            owner_id=owner_id,
            dedupe_key=f"media-embed:{owner_id}:root",
            max_attempts=3,
        )
    else:
        state.phase = "keyword"
    await db.commit()
    await jobs.kick(db)
    return {"changed_items": changed}


async def encode(texts: list[str], *, query: bool = False) -> list[list[float]]:
    settings = get_settings()
    if not settings.media_encoder_url:
        raise ProviderUnavailable("Local media encoder is not configured")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                settings.media_encoder_url.rstrip("/") + "/encode",
                json={"texts": texts, "query": query},
            )
            response.raise_for_status()
            data = response.json()
            vectors = data["vectors"]
            if data.get("model") != settings.media_encoder_model or len(vectors) != len(texts):
                raise ValueError("Encoder model or batch mismatch")
            import math

            if any(len(v) != 384 or not all(math.isfinite(float(n)) for n in v) for v in vectors):
                raise ValueError("Invalid embedding")
            return vectors
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise ProviderUnavailable("Local semantic search is temporarily unavailable") from exc


async def embed_corpus(db: AsyncSession, payload: dict) -> dict:
    owner_id = uuid.UUID(payload["owner_id"])
    model = get_settings().media_encoder_model
    pending = [
        MediaPassage.owner_id == owner_id,
        or_(MediaPassage.embedding.is_(None), MediaPassage.embedding_model != model),
    ]
    done = 0
    for _ in range(16):
        rows = (
            await db.execute(
                select(MediaPassage, MediaEntry.title, MediaEntry.title_en)
                .join(MediaEntry, MediaEntry.id == MediaPassage.entry_id)
                .where(*pending)
                .order_by(case((MediaEntry.kind == "movie", 0), else_=1), MediaPassage.id)
                .limit(16)
                .with_for_update(of=MediaPassage, skip_locked=True)
            )
        ).all()
        if not rows:
            break
        vectors = await encode(
            [
                " / ".join(filter(None, [title, english]))[:250] + "\n" + p.body[:2200]
                for p, title, english in rows
            ]
        )
        for (passage, _, _), vector in zip(rows, vectors, strict=True):
            passage.embedding, passage.embedding_model = vector, model
        await db.commit()
        done += len(rows)
    state = await index_state(db, owner_id)
    if await db.scalar(select(MediaPassage.id).where(*pending).limit(1)):
        await jobs.enqueue(
            db, EMBED_JOB, {"owner_id": str(owner_id)}, owner_id=owner_id, max_attempts=3
        )
        state.phase = "indexing"
    else:
        state.phase, state.error = "ready", None
    await db.commit()
    await jobs.kick(db)
    return {"embedded": done}


async def gave_up(db: AsyncSession, payload: dict, error: str) -> None:
    state = await index_state(db, uuid.UUID(payload["owner_id"]))
    state.phase, state.error = "error", "media_index_unavailable"
    await db.commit()
