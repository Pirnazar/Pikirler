from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import String, case, cast, func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound, ProviderUnavailable, ValidationFailed
from app.modules.pikirler.integrations import cinema as provider
from app.modules.pikirler.models.cinema import CinemaCue, CinemaMovie, CinemaSubtitle
from app.modules.pikirler.services import jobs

SUBTITLE_JOB = "cinema_subtitles"
METADATA_JOB = "cinema_metadata"
LOCALE_FIELDS = ("title", "description", "genres", "cast", "director", "runtime")


def english_metadata(movie: CinemaMovie) -> dict:
    return {**{key: getattr(movie, key) for key in LOCALE_FIELDS}, "source": "cinemeta"}


def language_code(value: str) -> str:
    value = value.lower().split("-")[0]
    return {"rus": "ru", "eng": "en", "tuk": "tk", "tur": "tr"}.get(value, value)


async def saved_languages(db: AsyncSession, movie: CinemaMovie) -> set[str]:
    values = (
        await db.execute(select(CinemaSubtitle.language).where(CinemaSubtitle.movie_id == movie.id))
    ).scalars()
    return {language_code(value) for value in values}


async def owned_movie(db: AsyncSession, owner_id: uuid.UUID, movie_id: uuid.UUID) -> CinemaMovie:
    movie = (
        await db.execute(
            select(CinemaMovie).where(CinemaMovie.id == movie_id, CinemaMovie.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    if movie is None:
        raise NotFound("Movie not found")
    return movie


def present(movie: CinemaMovie) -> dict[str, Any]:
    return {
        key: getattr(movie, key)
        for key in (
            "id",
            "imdb_id",
            "title",
            "year",
            "poster",
            "description",
            "runtime",
            "rating",
            "genres",
            "cast",
            "director",
            "subtitle_status",
            "subtitle_error",
            "subtitle_languages",
            "localized",
            "metadata_status",
            "watch_status",
            "is_favorite",
            "user_rating",
            "user_comment",
            "watched_at",
            "preferences_version",
            "created_at",
        )
    }


async def list_movies(
    db: AsyncSession,
    owner_id: uuid.UUID,
    query: str,
    offset: int,
    limit: int,
    watch_status: str = "",
    favorite: bool = False,
) -> dict:
    conditions = [CinemaMovie.owner_id == owner_id]
    if watch_status:
        conditions.append(CinemaMovie.watch_status == watch_status)
    if favorite:
        conditions.append(CinemaMovie.is_favorite.is_(True))
    if query.strip():
        pattern = (
            "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        conditions.append(
            or_(
                CinemaMovie.title.ilike(pattern),
                CinemaMovie.description.ilike(pattern),
                cast(CinemaMovie.localized, String).ilike(pattern),
                cast(CinemaMovie.aliases, String).ilike(pattern),
            )
        )
    total = await db.scalar(select(func.count()).select_from(CinemaMovie).where(*conditions))
    rows = (
        await db.execute(
            select(CinemaMovie)
            .where(*conditions)
            .order_by(CinemaMovie.created_at.desc(), CinemaMovie.id)
            .offset(offset)
            .limit(limit)
        )
    ).scalars()
    items = []
    for movie in rows:
        item = present(movie)
        item["subtitle_count"] = await db.scalar(
            select(func.count())
            .select_from(CinemaSubtitle)
            .where(CinemaSubtitle.movie_id == movie.id)
        )
        items.append(item)
    return {"items": items, "total": total}


async def detail(db: AsyncSession, owner_id: uuid.UUID, movie_id: uuid.UUID) -> dict:
    movie = await owned_movie(db, owner_id, movie_id)
    tracks = (
        await db.execute(
            select(CinemaSubtitle)
            .where(CinemaSubtitle.movie_id == movie.id)
            .order_by(
                case(
                    (CinemaSubtitle.language == "ru", 0),
                    (CinemaSubtitle.language == "en", 1),
                    else_=2,
                ),
                CinemaSubtitle.created_at.desc(),
            )
        )
    ).scalars()
    return {
        "movie": present(movie),
        "tracks": [
            {
                key: getattr(track, key)
                for key in ("id", "language", "source", "filename", "cue_count", "created_at")
            }
            for track in tracks
        ],
    }


async def queue_subtitles(db: AsyncSession, movie: CinemaMovie) -> None:
    saved = await saved_languages(db, movie)
    movie.subtitle_languages = {
        **movie.subtitle_languages,
        **{language: "ready" if language in saved else "pending" for language in ("ru", "en")},
    }
    movie.subtitle_status, movie.subtitle_error = "pending", None
    await jobs.enqueue(
        db,
        SUBTITLE_JOB,
        {"movie_id": str(movie.id), "owner_id": str(movie.owner_id)},
        owner_id=movie.owner_id,
        dedupe_key=f"cinema:{movie.id}",
        max_attempts=3,
    )


async def queue_metadata(db: AsyncSession, movie: CinemaMovie) -> None:
    movie.metadata_status = "pending"
    await jobs.enqueue(
        db,
        METADATA_JOB,
        {"movie_id": str(movie.id), "owner_id": str(movie.owner_id)},
        owner_id=movie.owner_id,
        dedupe_key=f"cinema-meta:{movie.id}",
        max_attempts=3,
    )


async def queue_enrichment(db: AsyncSession, movie: CinemaMovie) -> None:
    await queue_metadata(db, movie)
    await queue_subtitles(db, movie)


async def add_movie(db: AsyncSession, owner_id: uuid.UUID, imdb_id: str, alias: str) -> dict:
    existing = (
        await db.execute(
            select(CinemaMovie).where(
                CinemaMovie.owner_id == owner_id, CinemaMovie.imdb_id == imdb_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        if alias and alias not in existing.aliases:
            existing.aliases = [*existing.aliases, alias][:20]
        if existing.metadata_status != "ready":
            await queue_metadata(db, existing)
        if not {"ru", "en"}.issubset(await saved_languages(db, existing)):
            await queue_subtitles(db, existing)
        await db.commit()
        return present(existing)
    metadata = await provider.get_movie(imdb_id)
    movie_id = uuid.uuid4()
    inserted = (
        await db.execute(
            pg_insert(CinemaMovie)
            .values(
                id=movie_id,
                owner_id=owner_id,
                **metadata,
                aliases=[alias] if alias else [],
                subtitle_status="pending",
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "imdb_id"])
            .returning(CinemaMovie.id)
        )
    ).scalar_one_or_none()
    movie = (
        await db.execute(
            select(CinemaMovie).where(
                CinemaMovie.owner_id == owner_id, CinemaMovie.imdb_id == imdb_id
            )
        )
    ).scalar_one()
    if inserted:
        movie.localized = {"en": english_metadata(movie)}
        await queue_enrichment(db, movie)
    await db.commit()
    return present(movie)


async def save_track(
    db: AsyncSession,
    movie: CinemaMovie,
    data: bytes,
    language: str,
    source: str,
    provider_id: str,
    filename: str,
) -> CinemaSubtitle:
    cues = provider.parse_subtitle(data)
    language = language_code(language)
    # Lock only for the short write, after network retrieval and parsing.
    await db.refresh(movie, with_for_update=True)
    track_id = uuid.uuid4()
    inserted = (
        await db.execute(
            pg_insert(CinemaSubtitle)
            .values(
                id=track_id,
                movie_id=movie.id,
                language=language,
                source=source,
                provider_id=provider_id,
                filename=filename[:255],
                cue_count=len(cues),
            )
            .on_conflict_do_nothing(index_elements=["movie_id", "source", "provider_id"])
            .returning(CinemaSubtitle.id)
        )
    ).scalar_one_or_none()
    if inserted:
        for start in range(0, len(cues), 1000):
            await db.execute(
                insert(CinemaCue),
                [{"subtitle_id": inserted, **cue} for cue in cues[start : start + 1000]],
            )
    saved = await saved_languages(db, movie)
    movie.subtitle_status = (
        "ready"
        if {"ru", "en"}.issubset(saved)
        else "pending"
        if movie.subtitle_status == "pending"
        else "partial"
    )
    movie.subtitle_error = None
    movie.subtitle_languages = {**movie.subtitle_languages, language: "ready"}
    track = (
        await db.execute(
            select(CinemaSubtitle).where(
                CinemaSubtitle.movie_id == movie.id,
                CinemaSubtitle.source == source,
                CinemaSubtitle.provider_id == provider_id,
            )
        )
    ).scalar_one()
    from app.modules.pikirler.services.media_corpus import queue_sync

    await queue_sync(db, movie.owner_id)
    await db.commit()
    return track


async def upload(
    db: AsyncSession,
    owner_id: uuid.UUID,
    movie_id: uuid.UUID,
    data: bytes,
    language: str,
    filename: str,
) -> dict:
    movie = await owned_movie(db, owner_id, movie_id)
    track = await save_track(
        db, movie, data, language, "upload", hashlib.sha256(data).hexdigest(), filename
    )
    return {"id": track.id, "cue_count": track.cue_count}


async def fetch_track(
    db: AsyncSession, owner_id: uuid.UUID, movie_id: uuid.UUID, language: str, provider_id: str
) -> dict:
    movie = await owned_movie(db, owner_id, movie_id)
    options = await provider.subtitle_options(movie.imdb_id, language)
    choice = next((item for item in options if item["id"] == provider_id), None)
    if not choice:
        raise NotFound("Subtitle version is no longer available")
    data = await provider.download_subtitle(choice["url"])
    track = await save_track(
        db, movie, data, language, "opensubtitles", provider_id, choice["label"]
    )
    return {"id": track.id, "cue_count": track.cue_count}


def cue_conditions(query: str) -> list:
    if not query.strip():
        return []
    return [
        or_(
            *[
                CinemaCue.search_vector.op("@@")(func.websearch_to_tsquery(config, query.strip()))
                for config in ("simple", "russian", "english")
            ]
        )
    ]


async def quotes(
    db: AsyncSession,
    owner_id: uuid.UUID,
    query: str,
    offset: int,
    limit: int,
    movie_id: uuid.UUID | None = None,
    subtitle_id: uuid.UUID | None = None,
    language: str | None = None,
    around_ms: int | None = None,
) -> dict:
    conditions = [CinemaMovie.owner_id == owner_id, *cue_conditions(query)]
    if movie_id:
        await owned_movie(db, owner_id, movie_id)
        conditions.append(CinemaMovie.id == movie_id)
    if subtitle_id:
        conditions.append(CinemaSubtitle.id == subtitle_id)
    if language:
        codes = [language, provider.LANGUAGES.get(language, language)]
        conditions.append(
            or_(CinemaSubtitle.language.in_(codes), CinemaSubtitle.language.like(language + "-%"))
        )
    if around_ms is not None and movie_id and subtitle_id and not query.strip():
        before = await db.scalar(
            select(func.count())
            .select_from(CinemaCue)
            .join(CinemaSubtitle, CinemaCue.subtitle_id == CinemaSubtitle.id)
            .join(CinemaMovie, CinemaSubtitle.movie_id == CinemaMovie.id)
            .where(*conditions, CinemaCue.start_ms < around_ms)
        )
        offset = (int(before or 0) // limit) * limit
    joined = (
        select(CinemaCue, CinemaMovie, CinemaSubtitle)
        .join(CinemaSubtitle, CinemaCue.subtitle_id == CinemaSubtitle.id)
        .join(CinemaMovie, CinemaSubtitle.movie_id == CinemaMovie.id)
        .where(*conditions)
    )
    total = await db.scalar(select(func.count()).select_from(joined.subquery()))
    rows = (
        await db.execute(
            joined.order_by(CinemaMovie.created_at.desc(), CinemaCue.start_ms, CinemaCue.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "total": total,
        "offset": offset,
        "items": [
            {
                "id": cue.id,
                "text": cue.body,
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "movie_id": movie.id,
                "title": movie.title,
                "title_ru": (movie.localized.get("ru") or {}).get("title"),
                "title_en": movie.title,
                "year": movie.year,
                "language": track.language,
                "source": track.source,
                "subtitle_id": track.id,
            }
            for cue, movie, track in rows
        ],
    }


async def run_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    movie_id, owner_id = uuid.UUID(payload["movie_id"]), uuid.UUID(payload["owner_id"])
    movie = await owned_movie(db, owner_id, movie_id)
    saved = await saved_languages(db, movie)
    if {"ru", "en"}.issubset(saved):
        movie.subtitle_status, movie.subtitle_error = "ready", None
        movie.subtitle_languages = {**movie.subtitle_languages, "ru": "ready", "en": "ready"}
        await db.commit()
        return {"already_saved": True}
    states = {language: "ready" for language in saved}
    errors = []
    tracks = []
    for language in ("ru", "en"):
        if language in saved:
            continue
        try:
            options = await provider.subtitle_options(movie.imdb_id, language)
        except ProviderUnavailable as exc:
            errors.append(exc)
            states[language] = "failed"
            continue
        states[language] = "not_found"
        download_errors = []
        for choice in [item for item in options if not item.get("forced")][:3]:
            try:
                data = await provider.download_subtitle(choice["url"])
                track = await save_track(
                    db, movie, data, language, "opensubtitles", choice["id"], choice["label"]
                )
                tracks.append(
                    {
                        "track_id": str(track.id),
                        "cue_count": track.cue_count,
                        "language": language,
                    }
                )
                states[language] = "ready"
                break
            except ProviderUnavailable as exc:
                download_errors.append(exc)
            except (ValueError, ValidationFailed):
                continue
        if states[language] != "ready" and download_errors:
            states[language] = "failed"
            errors.extend(download_errors)
    await db.refresh(movie, with_for_update=True)
    saved = await saved_languages(db, movie)
    states.update({language: "ready" for language in saved})
    movie.subtitle_languages = {**movie.subtitle_languages, **states}
    movie.subtitle_status = (
        "pending"
        if errors
        else "ready"
        if {"ru", "en"}.issubset(saved)
        else "partial"
        if saved
        else "not_found"
    )
    movie.subtitle_error = "subtitle_provider_unavailable" if errors else None
    await db.commit()
    if errors:
        raise errors[0]
    return {"tracks": tracks, "languages": states, "not_found": not saved}


async def gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    movie = await owned_movie(db, uuid.UUID(payload["owner_id"]), uuid.UUID(payload["movie_id"]))
    saved = await saved_languages(db, movie)
    movie.subtitle_languages = {
        **movie.subtitle_languages,
        **{language: "ready" if language in saved else "failed" for language in ("ru", "en")},
    }
    movie.subtitle_status = (
        "ready" if {"ru", "en"}.issubset(saved) else "partial" if saved else "failed"
    )
    movie.subtitle_error = None if {"ru", "en"}.issubset(saved) else "subtitle_provider_unavailable"
    await db.commit()


async def metadata_job(db: AsyncSession, payload: dict[str, Any]) -> dict:
    movie = await owned_movie(db, uuid.UUID(payload["owner_id"]), uuid.UUID(payload["movie_id"]))
    russian = await provider.get_russian_movie(movie.imdb_id)
    await db.refresh(movie)
    movie.localized = {
        **movie.localized,
        "en": movie.localized.get("en") or english_metadata(movie),
        "ru": {key: russian.get(key) for key in (*LOCALE_FIELDS, "source", "source_url")},
    }
    names = [movie.title, russian["title"]]
    movie.aliases = list(dict.fromkeys([*names, *movie.aliases]))[:30]
    movie.metadata_status = "ready"
    from app.modules.pikirler.services.media_corpus import queue_sync

    await queue_sync(db, movie.owner_id)
    await db.commit()
    return {"languages": ["ru", "en"]}


async def metadata_gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    movie = await owned_movie(db, uuid.UUID(payload["owner_id"]), uuid.UUID(payload["movie_id"]))
    movie.metadata_status = "ready" if movie.localized.get("ru") else "failed"
    await db.commit()


async def update_preferences(
    db: AsyncSession, owner_id: uuid.UUID, movie_id: uuid.UUID, expected_version: int, values: dict
) -> dict:
    movie = (
        await db.execute(
            select(CinemaMovie)
            .where(CinemaMovie.id == movie_id, CinemaMovie.owner_id == owner_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not movie:
        raise NotFound("Movie not found")
    if movie.preferences_version != expected_version:
        raise Conflict("Movie preferences changed; reload before saving", code="version_conflict")
    for key in ("watch_status", "is_favorite", "user_rating", "user_comment"):
        if key in values:
            setattr(movie, key, values[key])
    if "watch_status" in values:
        movie.watched_at = (
            (movie.watched_at or utcnow()) if movie.watch_status == "watched" else None
        )
    movie.preferences_version += 1
    await db.commit()
    return present(movie)
