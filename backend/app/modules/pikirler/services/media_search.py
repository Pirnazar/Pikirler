from __future__ import annotations

import uuid

from sqlalchemy import case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ProviderUnavailable
from app.core.text import escape_like, prepare_query
from app.modules.pikirler.models.media_search import MediaEntry, MediaIndexState, MediaPassage
from app.modules.pikirler.services import media_corpus


async def status(db: AsyncSession, owner_id: uuid.UUID) -> dict:
    state = await db.get(MediaIndexState, owner_id)
    entries = await db.scalar(
        select(func.count()).select_from(MediaEntry).where(MediaEntry.owner_id == owner_id)
    )
    total = await db.scalar(
        select(func.count()).select_from(MediaPassage).where(MediaPassage.owner_id == owner_id)
    )
    embedded = await db.scalar(
        select(func.count())
        .select_from(MediaPassage)
        .where(
            MediaPassage.owner_id == owner_id,
            MediaPassage.embedding.is_not(None),
            MediaPassage.embedding_model == get_settings().media_encoder_model,
        )
    )
    phase = state.phase if state else "empty"
    if phase == "ready" and get_settings().media_encoder_url and embedded < total:
        phase = "indexing"
    return {
        "entries": entries,
        "passages": total,
        "embedded": embedded,
        "phase": phase,
        "error": state.error if state else None,
        "processed_items": state.processed_items if state else 0,
        "total_items": state.total_items if state else 0,
        "last_sync_at": state.last_sync_at if state else None,
        "semantic_enabled": bool(get_settings().media_encoder_url),
    }


async def facets(db: AsyncSession, owner_id: uuid.UUID) -> dict:
    entries = (
        await db.execute(
            select(MediaEntry.year, MediaEntry.genres).where(MediaEntry.owner_id == owner_id)
        )
    ).all()
    languages = list(
        (
            await db.execute(
                select(MediaPassage.language).where(MediaPassage.owner_id == owner_id).distinct()
            )
        ).scalars()
    )
    return {
        "years": sorted({year for year, _ in entries if year}, reverse=True),
        "genres": sorted({str(genre) for _, genres in entries for genre in genres}),
        "languages": sorted(languages),
    }


def conditions(owner_id: uuid.UUID, kind: str, language: str, genre: str, year: int | None) -> list:
    result = [MediaEntry.owner_id == owner_id, MediaPassage.owner_id == owner_id]
    if kind == "lyrics":
        result.append(MediaPassage.part == "lyrics")
    elif kind:
        result.append(MediaEntry.kind == kind)
    if language:
        result.append(MediaPassage.language == language)
    if genre:
        result.append(MediaEntry.genres.contains([genre]))
    if year:
        result.append(MediaEntry.year == year)
    return result


def joined():
    return select(MediaPassage, MediaEntry).join(MediaEntry, MediaPassage.entry_id == MediaEntry.id)


def present(passage: MediaPassage, entry: MediaEntry, score: float = 0) -> dict:
    first = passage.segments[0] if passage.segments else {}
    if entry.movie_id:
        url = f"#/media/movies/{entry.movie_id}"
        if first.get("track_id"):
            url += f"?track={first['track_id']}&at={passage.start_ms or 0}"
    else:
        video_id = entry.source_key.removeprefix("youtube:")
        url = f"https://www.youtube.com/watch?v={video_id}"
        if passage.start_ms is not None:
            url += f"&t={passage.start_ms // 1000}"
    return {
        "id": passage.id,
        "entry_id": entry.id,
        "kind": entry.kind,
        "part": passage.part,
        "title": entry.title,
        "title_en": entry.title_en,
        "creator": entry.creator,
        "year": entry.year,
        "genres": entry.genres,
        "poster": entry.poster,
        "language": passage.language,
        "text": passage.body,
        "segments": passage.segments,
        "start_ms": passage.start_ms,
        "end_ms": passage.end_ms,
        "url": url,
        "score": round(float(score), 5),
    }


async def search(
    db: AsyncSession,
    owner_id: uuid.UUID,
    query: str,
    mode: str,
    kind: str = "",
    language: str = "",
    genre: str = "",
    year: int | None = None,
    offset: int = 0,
    limit: int = 20,
) -> dict:
    filters = conditions(owner_id, kind, language, genre, year)
    norm, folded = prepare_query(query)
    queries = [
        func.websearch_to_tsquery(cast(literal(config), REGCONFIG), value)
        for config, value in (("simple", folded), ("russian", norm), ("english", norm))
    ]
    keyword_filter = or_(*[MediaPassage.search_vector.op("@@")(q) for q in queries])
    rank = func.greatest(*[func.ts_rank_cd(MediaPassage.search_vector, q) for q in queries])
    keyword = joined().where(*filters, keyword_filter)
    total_keyword = 0
    keyword_rows = []
    if mode != "semantic":
        total_keyword = await db.scalar(select(func.count()).select_from(keyword.subquery()))
        if total_keyword == 0:
            keyword = joined().where(
                *filters, MediaPassage.body.ilike("%" + escape_like(query.strip()) + "%")
            )
            total_keyword = await db.scalar(select(func.count()).select_from(keyword.subquery()))
        stmt = keyword.order_by(
            rank.desc(), case((MediaPassage.part == "metadata", 0), else_=1), MediaPassage.id
        )
        keyword_rows = (
            await db.execute(
                stmt.offset(offset if mode == "keyword" else 0).limit(
                    limit if mode == "keyword" else 200
                )
            )
        ).all()
        if mode == "keyword":
            return {
                "items": [present(p, e) for p, e in keyword_rows],
                "total": total_keyword,
                "semantic_status": "not_requested",
            }
    semantic_status = "ok"
    semantic_rows = []
    try:
        vectors = await media_corpus.encode([query], query=True)
        distance = MediaPassage.embedding.cosine_distance(vectors[0])
        semantic_rows = (
            await db.execute(
                joined()
                .where(
                    *filters,
                    MediaPassage.embedding.is_not(None),
                    MediaPassage.embedding_model == get_settings().media_encoder_model,
                    distance <= 0.4,
                )
                .order_by(distance, MediaPassage.id)
                .limit(200)
            )
        ).all()
    except ProviderUnavailable:
        if mode == "semantic":
            raise
        semantic_status = "unavailable"
    scores, rows = {}, {}
    for ranking in (keyword_rows, semantic_rows):
        for index, (passage, entry) in enumerate(ranking, 1):
            scores[passage.id] = scores.get(passage.id, 0) + 1 / (60 + index)
            rows[passage.id] = (passage, entry)
    ordered = sorted(scores, key=lambda pid: scores[pid], reverse=True)
    return {
        "items": [present(*rows[pid], scores[pid]) for pid in ordered[offset : offset + limit]],
        "total": len(ordered),
        "semantic_status": semantic_status,
        "candidate_limit": 200 if mode == "semantic" else 400,
    }
