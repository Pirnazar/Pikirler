from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, File, Form, Query, UploadFile
from pydantic import BaseModel, Field

from app.api.deps import DB, Account
from app.core.exceptions import PayloadTooLarge
from app.modules.pikirler.integrations import cinema as provider
from app.modules.pikirler.services import cinema as service

router = APIRouter(prefix="/movies", tags=["cinema"])


class MovieCreate(BaseModel):
    imdb_id: str = Field(pattern=r"^tt\d{7,12}$")
    query: str = Field(default="", max_length=120)


class SubtitleChoice(BaseModel):
    language: str = Field(default="ru", pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
    provider_id: str = Field(min_length=1, max_length=64)


class MoviePreferences(BaseModel):
    expected_version: int = Field(ge=1)
    watch_status: Literal["unlisted", "want", "watched"] | None = None
    is_favorite: bool | None = None
    user_rating: int | None = Field(default=None, ge=1, le=10)
    user_comment: str | None = Field(default=None, max_length=10000)


@router.get("/catalog")
async def catalog(principal: Account, q: str = Query(min_length=2, max_length=120)) -> dict:
    return {"items": await provider.search_movies(q.strip())}


@router.get("/quotes")
async def quotes(
    principal: Account,
    db: DB,
    q: str = Query(min_length=1, max_length=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(30, ge=1, le=100),
    language: str | None = Query(None, pattern=r"^[a-z]{2,3}$"),
) -> dict:
    return await service.quotes(db, principal.owner_id, q, offset, limit, language=language)


@router.get("")
async def list_movies(
    principal: Account,
    db: DB,
    q: str = Query("", max_length=120),
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(24, ge=1, le=100),
    watch_status: Literal["", "unlisted", "want", "watched"] = "",
    favorite: bool = False,
) -> dict:
    return await service.list_movies(
        db, principal.owner_id, q, offset, limit, watch_status, favorite
    )


@router.post("", status_code=201)
async def add_movie(body: MovieCreate, principal: Account, db: DB) -> dict:
    return await service.add_movie(db, principal.owner_id, body.imdb_id, body.query.strip())


@router.get("/{movie_id}")
async def detail(movie_id: uuid.UUID, principal: Account, db: DB) -> dict:
    return await service.detail(db, principal.owner_id, movie_id)


@router.patch("/{movie_id}/preferences")
async def preferences(
    movie_id: uuid.UUID, body: MoviePreferences, principal: Account, db: DB
) -> dict:
    values = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    values = {
        key: value for key, value in values.items() if value is not None or key == "user_rating"
    }
    return await service.update_preferences(
        db, principal.owner_id, movie_id, body.expected_version, values
    )


@router.post("/{movie_id}/subtitles/auto", status_code=202)
async def auto_subtitles(movie_id: uuid.UUID, principal: Account, db: DB) -> dict:
    movie = await service.owned_movie(db, principal.owner_id, movie_id)
    await service.queue_subtitles(db, movie)
    await db.commit()
    return {"status": "pending"}


@router.post("/{movie_id}/refresh", status_code=202)
async def refresh_movie(movie_id: uuid.UUID, principal: Account, db: DB) -> dict:
    movie = await service.owned_movie(db, principal.owner_id, movie_id)
    await service.queue_enrichment(db, movie)
    await db.commit()
    return {"status": "pending"}


@router.get("/{movie_id}/subtitles/options")
async def subtitle_options(
    movie_id: uuid.UUID,
    principal: Account,
    db: DB,
    language: str = Query("ru", pattern=r"^[a-z]{2,3}$"),
) -> dict:
    movie = await service.owned_movie(db, principal.owner_id, movie_id)
    items = await provider.subtitle_options(movie.imdb_id, language)
    return {"items": [{key: item[key] for key in ("id", "language", "label")} for item in items]}


@router.post("/{movie_id}/subtitles/fetch")
async def fetch_subtitle(
    movie_id: uuid.UUID, body: SubtitleChoice, principal: Account, db: DB
) -> dict:
    return await service.fetch_track(
        db, principal.owner_id, movie_id, body.language, body.provider_id
    )


@router.post("/{movie_id}/subtitles/upload", status_code=201)
async def upload_subtitle(
    movie_id: uuid.UUID,
    principal: Account,
    db: DB,
    file: UploadFile = File(),
    language: str = Form("ru", pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$"),
) -> dict:
    await service.owned_movie(db, principal.owner_id, movie_id)
    try:
        content = await file.read(provider.MAX_SUBTITLE_BYTES + 1)
        if len(content) > provider.MAX_SUBTITLE_BYTES:
            raise PayloadTooLarge("Subtitle file exceeds 5 MB")
        return await service.upload(
            db, principal.owner_id, movie_id, content, language, file.filename or "subtitles.srt"
        )
    finally:
        await file.close()


@router.get("/{movie_id}/cues")
async def cues(
    movie_id: uuid.UUID,
    principal: Account,
    db: DB,
    q: str = Query("", max_length=200),
    subtitle_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0, le=1_000_000),
    limit: int = Query(50, ge=1, le=200),
    around_ms: int | None = Query(None, ge=0, le=200_000_000),
) -> dict:
    return await service.quotes(
        db, principal.owner_id, q, offset, limit, movie_id, subtitle_id, around_ms=around_ms
    )
