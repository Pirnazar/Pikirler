"""Read-only access to the owner's Second Brain YouTube archive."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Account
from app.core.config import get_settings

router = APIRouter(prefix="/youtube-archive", tags=["youtube archive"])


@contextmanager
def archive() -> Iterator[sqlite3.Connection | None]:
    path = Path(os.environ.get("PIKIRLER_YOUTUBE_ARCHIVE", "/srv/youtube-archive/youtube.sqlite3"))
    try:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    except sqlite3.OperationalError:
        if path.exists():
            raise
        yield None
        return
    try:
        db.row_factory = sqlite3.Row
        db.create_function("fold", 1, lambda value: str(value or "").casefold())
        yield db
    finally:
        db.close()


@router.get("")
def list_videos(
    principal: Account,
    q: str = Query("", max_length=200),
    kind: str = Query("", pattern=r"^(|video|music|unknown)$"),
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(30, ge=1, le=100),
) -> dict:
    owner = get_settings().youtube_archive_owner_id
    if not owner or str(principal.owner_id) != owner:
        return {"configured": False, "items": [], "total": 0}
    filters = []
    params = {"kind": kind, "q": q.strip().casefold(), "limit": limit, "offset": offset}
    if kind:
        filters.append("v.content_kind = :kind")
    if params["q"]:
        filters.append("""
            (instr(fold(v.title), :q) OR instr(fold(v.channel_title), :q)
             OR instr(fold(v.artist), :q) OR instr(fold(v.description), :q)
             OR (t.status = 'ok' AND instr(fold(t.full_text), :q))
             OR (l.status = 'ok' AND instr(fold(l.full_text), :q)))
        """)
    where = " WHERE " + " AND ".join(filters) if filters else ""
    joined = (
        """
        FROM youtube_videos v
        LEFT JOIN youtube_transcripts t ON t.video_id = v.id
        LEFT JOIN song_lyrics l ON l.video_id = v.id
    """
        + where
    )
    with archive() as db:
        if db is None:
            return {"configured": False, "items": [], "total": 0}
        total = db.execute("SELECT count(*) " + joined, params).fetchone()[0]
        rows = db.execute(
            """
            SELECT v.youtube_video_id, v.title, v.channel_title, v.artist, v.liked_at,
                   v.duration_seconds, v.content_kind, v.thumbnail_url, v.is_available,
                   t.status AS transcript_status, l.status AS lyrics_status
        """
            + joined
            + " ORDER BY v.liked_at DESC NULLS LAST, v.id DESC LIMIT :limit OFFSET :offset",
            params,
        )
        return {"configured": True, "items": [dict(row) for row in rows], "total": total}


@router.get("/{video_id}")
def video_detail(video_id: str, principal: Account) -> dict:
    owner = get_settings().youtube_archive_owner_id
    if not owner or str(principal.owner_id) != owner:
        raise HTTPException(404, "Video not found")
    with archive() as db:
        if db is None:
            raise HTTPException(404, "YouTube archive is unavailable")
        video = db.execute(
            "SELECT * FROM youtube_videos WHERE youtube_video_id = ?", (video_id,)
        ).fetchone()
        if video is None:
            raise HTTPException(404, "Video not found")
        result = {"video": dict(video)}
        for key, table, lines, foreign_key, position in (
            (
                "transcript",
                "youtube_transcripts",
                "youtube_transcript_segments",
                "transcript_id",
                "segment_index",
            ),
            ("lyrics", "song_lyrics", "song_lyric_lines", "lyrics_id", "line_index"),
        ):
            track = db.execute(
                # Table names come exclusively from the fixed tuples above.
                f"SELECT id, source, language_code, status, full_text FROM {table} "  # noqa: S608
                "WHERE video_id = ?",
                (video["id"],),
            ).fetchone()
            result[key] = dict(track) if track else None
            if track:
                result[key]["lines"] = (
                    [
                        dict(row)
                        for row in db.execute(
                            f"SELECT text, start_seconds, {position} AS position FROM {lines} "  # noqa: S608
                            f"WHERE {foreign_key} = ? ORDER BY {position}",
                            (track["id"],),
                        )
                    ]
                    if track["status"] == "ok"
                    else []
                )
        return result
