from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DB, Account
from app.core.exceptions import ValidationFailed
from app.modules.pikirler.services import media_corpus, media_quotes, media_search

router = APIRouter(prefix="/media-search", tags=["media search"])


class Search(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    mode: Literal["keyword", "semantic", "hybrid"] = "keyword"
    kind: Literal["", "movie", "video", "music", "lyrics", "unknown"] = ""
    language: str = Field(default="", pattern=r"^(?:[a-z]{2,3})?$")
    genre: str = Field(default="", max_length=100)
    year: int | None = Field(default=None, ge=1880, le=2200)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=20, ge=1, le=50)


class Quote(BaseModel):
    source: Literal["cinema", "passage", "youtube_transcript", "lyrics"]
    reference: str = Field(min_length=1, max_length=128)
    position: int | None = Field(default=None, ge=0, le=1000000)


@router.post("")
async def search(body: Search, principal: Account, db: DB) -> dict:
    if not body.query.strip():
        raise ValidationFailed("Search query is empty")
    return await media_search.search(
        db,
        principal.owner_id,
        body.query.strip(),
        body.mode,
        body.kind,
        body.language,
        body.genre,
        body.year,
        body.offset,
        body.limit,
    )


@router.get("/status")
async def status(principal: Account, db: DB) -> dict:
    return await media_search.status(db, principal.owner_id)


@router.get("/facets")
async def facets(principal: Account, db: DB) -> dict:
    return await media_search.facets(db, principal.owner_id)


@router.post("/refresh", status_code=202)
async def refresh(principal: Account, db: DB) -> dict:
    await media_corpus.queue_sync(db, principal.owner_id)
    await db.commit()
    return {"status": "queued"}


@router.post("/quotes", status_code=201)
async def save_quote(body: Quote, principal: Account, db: DB) -> dict:
    return await media_quotes.save(db, principal, body.source, body.reference, body.position)
