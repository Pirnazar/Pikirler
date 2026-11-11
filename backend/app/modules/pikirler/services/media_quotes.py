from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.youtube_archive import archive
from app.core.config import get_settings
from app.core.exceptions import NotFound, ValidationFailed
from app.modules.pikirler.models import Thought
from app.modules.pikirler.models.cinema import CinemaCue, CinemaMovie, CinemaSubtitle
from app.modules.pikirler.models.media_search import MediaEntry, MediaPassage, MediaSavedQuote
from app.modules.pikirler.schemas.thoughts import ThoughtCreate
from app.modules.pikirler.services import thoughts
from app.modules.pikirler.services.auth import Principal


def timestamp(ms: int | None) -> str:
    if ms is None:
        return ""
    seconds = max(0, ms // 1000)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


async def resolve(
    db: AsyncSession, owner_id: uuid.UUID, source: str, reference: str, position: int | None
) -> dict:
    if source == "cinema":
        try:
            cue_id = int(reference)
        except ValueError as exc:
            raise ValidationFailed("Invalid quote reference") from exc
        row = (
            await db.execute(
                select(CinemaCue, CinemaSubtitle, CinemaMovie)
                .join(CinemaSubtitle, CinemaCue.subtitle_id == CinemaSubtitle.id)
                .join(CinemaMovie, CinemaSubtitle.movie_id == CinemaMovie.id)
                .where(CinemaCue.id == cue_id, CinemaMovie.owner_id == owner_id)
            )
        ).first()
        if not row:
            raise NotFound("Quote not found")
        cue, track, movie = row
        title = (movie.localized.get("ru") or {}).get("title") or movie.title
        return {
            "source": source,
            "reference": reference,
            "text": cue.body,
            "title": title,
            "title_en": movie.title,
            "year": movie.year,
            "language": track.language,
            "start_ms": cue.start_ms,
            "end_ms": cue.end_ms,
            "movie_id": str(movie.id),
            "track_id": str(track.id),
            "url": f"#/media/movies/{movie.id}?track={track.id}&at={cue.start_ms}",
        }
    if source == "passage":
        try:
            pid = uuid.UUID(reference)
        except ValueError as exc:
            raise ValidationFailed("Invalid quote reference") from exc
        row = (
            await db.execute(
                select(MediaPassage, MediaEntry)
                .join(MediaEntry, MediaPassage.entry_id == MediaEntry.id)
                .where(
                    MediaPassage.id == pid,
                    MediaPassage.owner_id == owner_id,
                    MediaEntry.owner_id == owner_id,
                )
            )
        ).first()
        if not row:
            raise NotFound("Quote not found")
        passage, entry = row
        from app.modules.pikirler.services.media_search import present

        data = present(passage, entry)
        if position is not None:
            if not 0 <= position < len(passage.segments):
                raise ValidationFailed("Quote line not found")
            line = passage.segments[position]
            data.update(text=line["text"], start_ms=line.get("start_ms"), end_ms=line.get("end_ms"))
            if entry.movie_id and line.get("track_id"):
                data["url"] = (
                    f"#/media/movies/{entry.movie_id}?track={line['track_id']}"
                    f"&at={line.get('start_ms') or 0}"
                )
            elif not entry.movie_id:
                data["url"] = (
                    "https://www.youtube.com/watch?v="
                    + entry.source_key.removeprefix("youtube:")
                    + f"&t={(line.get('start_ms') or 0) // 1000}"
                )
        return {
            key: data.get(key)
            for key in (
                "text",
                "title",
                "title_en",
                "year",
                "language",
                "start_ms",
                "end_ms",
                "url",
                "kind",
                "part",
            )
        } | {"source": source, "reference": reference, "position": position}
    if (
        source not in {"youtube_transcript", "lyrics"}
        or str(owner_id) != get_settings().youtube_archive_owner_id
    ):
        raise NotFound("Quote not found")
    with archive() as connection:
        if connection is None:
            raise NotFound("Archive unavailable")
        video = connection.execute(
            "SELECT id,title,youtube_video_id FROM youtube_videos WHERE youtube_video_id=?",
            (reference,),
        ).fetchone()
        if not video:
            raise NotFound("Quote not found")
        track_sql, line_sql = (
            (
                "SELECT id,language_code FROM youtube_transcripts WHERE video_id=? AND status='ok'",
                "SELECT text,start_seconds FROM youtube_transcript_segments "
                "WHERE transcript_id=? AND segment_index=?",
            )
            if source == "youtube_transcript"
            else (
                "SELECT id,language_code FROM song_lyrics WHERE video_id=? AND status='ok'",
                "SELECT text,start_seconds FROM song_lyric_lines "
                "WHERE lyrics_id=? AND line_index=?",
            )
        )
        track = connection.execute(track_sql, (video["id"],)).fetchone()
        line = (
            connection.execute(
                line_sql,
                (track["id"], position),
            ).fetchone()
            if track and position is not None
            else None
        )
        if not line:
            raise NotFound("Quote not found")
        ms = int(line["start_seconds"] * 1000) if line["start_seconds"] is not None else None
        return {
            "source": source,
            "reference": reference,
            "position": position,
            "text": line["text"],
            "title": video["title"],
            "title_en": None,
            "language": track["language_code"] or "und",
            "start_ms": ms,
            "end_ms": None,
            "url": f"https://www.youtube.com/watch?v={reference}"
            + (f"&t={ms // 1000}" if ms is not None else ""),
        }


async def save(
    db: AsyncSession, principal: Principal, source: str, reference: str, position: int | None
) -> dict:
    snapshot = await resolve(db, principal.owner_id, source, reference, position)
    quote_key = hashlib.sha256(
        f"{source}:{reference}:{position}:{snapshot['text']}".encode()
    ).hexdigest()
    lock_key = int(quote_key[:15], 16)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    previous = (
        await db.execute(
            select(MediaSavedQuote).where(
                MediaSavedQuote.owner_id == principal.owner_id,
                MediaSavedQuote.quote_key == quote_key,
            )
        )
    ).scalar_one_or_none()
    thought = await db.get(Thought, previous.thought_id) if previous else None
    if thought and thought.deleted_at is None:
        return {"thought_id": thought.id, "already_saved": True}
    caption = " · ".join(
        str(value)
        for value in [
            snapshot["title"],
            snapshot.get("title_en") if snapshot.get("title_en") != snapshot["title"] else None,
            snapshot.get("year"),
            str(snapshot["language"]).upper(),
            timestamp(snapshot.get("start_ms")),
        ]
        if value
    )
    content = snapshot["text"] + "\n\n— " + caption
    thought = await thoughts.create(
        db,
        principal,
        ThoughtCreate(content=content),
        source_metadata={"media_quote": snapshot},
        commit=False,
    )
    if previous:
        previous.thought_id = thought.id
    else:
        db.add(
            MediaSavedQuote(owner_id=principal.owner_id, thought_id=thought.id, quote_key=quote_key)
        )
    await db.commit()
    return {"thought_id": thought.id, "already_saved": False}
