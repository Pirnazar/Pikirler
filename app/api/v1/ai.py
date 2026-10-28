"""AI over the owner's thoughts. Every response is marked ``generated: true``
and lists the thoughts it is based on. Nothing here modifies a thought."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from pydantic import Field, model_validator

from app.api.deps import DB, AIAccess
from app.core.config import get_settings
from app.core.text import snippet
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.services import ai as service

from .search import SearchFilters

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    responses={
        **ERROR_RESPONSES,
        403: {"description": "External AI not enabled in settings"},
        429: {"description": "Daily AI budget exhausted"},
        503: {"description": "AI provider disabled, unreachable or timed out"},
    },
)


class SourceRef(Schema):
    thought_id: uuid.UUID
    created_at: datetime
    snippet: str
    url: str


class Generated(Schema):
    generated: bool = Field(default=True, description="Always true: machine-generated content")
    provider: str
    model: str
    generated_at: datetime
    sources: list[SourceRef]


class AskRequest(Schema):
    question: str = Field(min_length=1, max_length=2000)
    filters: SearchFilters = Field(default_factory=SearchFilters)
    max_sources: int = Field(default=12, ge=1, le=50)


class AskResponse(Generated):
    question: str
    answer: str | None
    insufficient_data: bool
    llm_called: bool
    semantic: str
    period: tuple[datetime, datetime] | None = None


class TargetRequest(Schema):
    thought_id: uuid.UUID | None = None
    thread_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> TargetRequest:
        if (self.thought_id is None) == (self.thread_id is None):
            raise ValueError("give exactly one of thought_id or thread_id")
        return self


class ThoughtRequest(Schema):
    thought_id: uuid.UUID


class RelatedRequest(ThoughtRequest):
    limit: int = Field(default=8, ge=1, le=30)
    suggest_relations: bool = Field(
        default=True, description="Ask the LLM to label relations; stored as suggestions only"
    )


class SummaryResponse(Generated):
    summary: str
    key_points: list[str]


class RelationSuggestion(Schema):
    thought_id: uuid.UUID
    relation_type: str
    confidence: float
    reason: str | None


class RelatedResponse(Schema):
    generated: bool = True
    thought_id: uuid.UUID
    similar: list[SourceRef]
    semantic: str
    suggestions: list[RelationSuggestion]
    provider: str | None = None
    model: str | None = None


class Stage(Schema):
    thought_id: uuid.UUID
    summary: str


class Contradiction(Schema):
    a: uuid.UUID
    b: uuid.UUID
    explanation: str


class EvolutionResponse(Generated):
    narrative: str
    stages: list[Stage]
    contradictions: list[Contradiction]


class ExpandResponse(Generated):
    analysis: str
    next_steps: list[str]
    open_questions: list[str]


class ActionItem(Schema):
    text: str
    source_thought_id: uuid.UUID


class ActionsResponse(Generated):
    actions: list[ActionItem]


async def _sources(db: DB, owner_id: uuid.UUID, ids: list[uuid.UUID]) -> list[SourceRef]:
    prefix = get_settings().api_prefix
    return [
        SourceRef(
            thought_id=t.id,
            created_at=t.created_at,
            snippet=snippet(t.content, ""),
            url=f"{prefix}/thoughts/{t.id}",
        )
        for t in await service.thoughts_by_ids(db, owner_id, ids)
    ]


async def _with_sources(db: DB, owner_id: uuid.UUID, data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "sources": await _sources(db, owner_id, list(data.get("sources", [])))}


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, principal: AIAccess, db: DB) -> AskResponse:
    """ "What did I think about Second Brain last month?" -- answered only from
    the owner's thoughts, with references."""
    data = await service.ask(
        db, principal, body.question, body.filters.to_filters(), body.max_sources
    )
    return AskResponse.model_validate(await _with_sources(db, principal.owner_id, data))


@router.post("/summarize", response_model=SummaryResponse)
async def summarize(body: TargetRequest, principal: AIAccess, db: DB) -> SummaryResponse:
    data = await service.summarize(db, principal, body.thought_id, body.thread_id)
    return SummaryResponse.model_validate(await _with_sources(db, principal.owner_id, data))


@router.post("/related", response_model=RelatedResponse)
async def related(body: RelatedRequest, principal: AIAccess, db: DB) -> RelatedResponse:
    data = await service.related(db, principal, body.thought_id, body.limit, body.suggest_relations)
    data["similar"] = await _sources(db, principal.owner_id, data["similar"])
    return RelatedResponse.model_validate(data)


@router.post("/evolution", response_model=EvolutionResponse)
async def evolution(body: ThoughtRequest, principal: AIAccess, db: DB) -> EvolutionResponse:
    data = await service.evolution(db, principal, body.thought_id)
    return EvolutionResponse.model_validate(await _with_sources(db, principal.owner_id, data))


@router.post("/expand", response_model=ExpandResponse)
async def expand(body: ThoughtRequest, principal: AIAccess, db: DB) -> ExpandResponse:
    data = await service.expand(db, principal, body.thought_id)
    return ExpandResponse.model_validate(await _with_sources(db, principal.owner_id, data))


@router.post("/extract-actions", response_model=ActionsResponse)
async def extract_actions(body: TargetRequest, principal: AIAccess, db: DB) -> ActionsResponse:
    data = await service.extract_actions(db, principal, body.thought_id, body.thread_id)
    return ActionsResponse.model_validate(await _with_sources(db, principal.owner_id, data))
