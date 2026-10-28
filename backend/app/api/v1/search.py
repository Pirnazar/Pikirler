from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import Field

from app.api.deps import DB, SearchAccess
from app.core.config import get_settings
from app.core.text import snippet
from app.modules.pikirler.models.enums import ThoughtStatus, ThoughtType
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.repositories.thoughts import ThoughtFilters
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.services import search as service
from app.modules.pikirler.services.search import Hit

router = APIRouter(tags=["search"], responses=ERROR_RESPONSES)


class SearchFilters(Schema):
    type: ThoughtType | None = None
    status: ThoughtStatus | None = None
    tag: str | None = Field(default=None, max_length=64)
    project_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    def to_filters(self) -> ThoughtFilters:
        return ThoughtFilters(
            thought_type=self.type.value if self.type else None,
            status=self.status.value if self.status else None,
            tag=self.tag,
            project_id=self.project_id,
            thread_id=self.thread_id,
            date_from=self.date_from,
            date_to=self.date_to,
        )


class SearchRequest(Schema):
    query: str = Field(min_length=1, max_length=1000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    limit: int = Field(default=20, ge=1, le=100)


class SearchResult(Schema):
    thought_id: uuid.UUID
    snippet: str
    created_at: datetime
    occurred_at: datetime | None
    thought_type: str
    status: str
    tags: list[str]
    thread_id: uuid.UUID | None
    match_type: str = Field(description="keyword | fuzzy | semantic | hybrid")
    score: float
    url: str


class SearchResponse(Schema):
    query: str
    results: list[SearchResult]
    semantic: str = Field(
        description="ok | disabled | unavailable | not_permitted | not_requested | no_embedding"
    )


async def build_results(
    db: DB, owner_id: uuid.UUID, hits: list[Hit], query: str
) -> list[SearchResult]:
    thoughts = await repo.load_many(db, owner_id, [h.thought_id for h in hits])
    prefix = get_settings().api_prefix
    out = []
    for hit in hits:
        t = thoughts.get(hit.thought_id)
        if t is None:
            continue
        out.append(
            SearchResult(
                thought_id=t.id,
                snippet=snippet(t.content, query),
                created_at=t.created_at,
                occurred_at=t.occurred_at,
                thought_type=t.thought_type,
                status=t.status,
                tags=sorted(link.tag.name for link in t.tag_links),
                thread_id=t.thread_id,
                match_type=hit.match_type,
                score=round(hit.score, 6),
                url=f"{prefix}/thoughts/{t.id}",
            )
        )
    return out


@router.get("/search", response_model=SearchResponse)
async def keyword_search(
    principal: SearchAccess,
    db: DB,
    q: str = Query(
        min_length=1,
        max_length=1000,
        description='Words, "exact phrase", or, -exclude. Any language.',
    ),
    type: ThoughtType | None = None,
    status: ThoughtStatus | None = None,
    tag: str | None = Query(default=None, max_length=64),
    project_id: uuid.UUID | None = None,
    thread_id: uuid.UUID | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> SearchResponse:
    filters = SearchFilters(
        type=type,
        status=status,
        tag=tag,
        project_id=project_id,
        thread_id=thread_id,
        date_from=date_from,
        date_to=date_to,
    )
    hits = await service.keyword(db, principal.owner_id, q, filters.to_filters(), limit)
    return SearchResponse(
        query=q,
        results=await build_results(db, principal.owner_id, hits, q),
        semantic="not_requested",
    )


@router.post("/search/semantic", response_model=SearchResponse)
async def semantic_search(body: SearchRequest, principal: SearchAccess, db: DB) -> SearchResponse:
    hits = await service.semantic(
        db, principal.owner_id, body.query, body.filters.to_filters(), body.limit
    )
    return SearchResponse(
        query=body.query,
        semantic="ok",
        results=await build_results(db, principal.owner_id, hits, body.query),
    )


@router.post("/search/hybrid", response_model=SearchResponse)
async def hybrid_search(body: SearchRequest, principal: SearchAccess, db: DB) -> SearchResponse:
    outcome = await service.hybrid(
        db, principal.owner_id, body.query, body.filters.to_filters(), body.limit
    )
    return SearchResponse(
        query=body.query,
        semantic=outcome.semantic,
        results=await build_results(db, principal.owner_id, outcome.hits, body.query),
    )


@router.get("/thoughts/{thought_id}/similar", response_model=SearchResponse)
async def similar(
    thought_id: uuid.UUID,
    principal: SearchAccess,
    db: DB,
    limit: int = Query(default=10, ge=1, le=50),
) -> SearchResponse:
    outcome = await service.similar(db, principal.owner_id, thought_id, limit)
    return SearchResponse(
        query="",
        semantic=outcome.semantic,
        results=await build_results(db, principal.owner_id, outcome.hits, ""),
    )
