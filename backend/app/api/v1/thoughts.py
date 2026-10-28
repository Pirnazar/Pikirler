from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, ReadThoughts, WriteThoughts
from app.modules.pikirler.models.enums import ThoughtStatus, ThoughtType
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.repositories.thoughts import ThoughtFilters
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Page
from app.modules.pikirler.schemas.organize import RelationCreate, RelationOut
from app.modules.pikirler.schemas.thoughts import (
    ReplyCreate,
    RevisionOut,
    RevisionRestore,
    ThoughtCreate,
    ThoughtOut,
    ThoughtUpdate,
)
from app.modules.pikirler.services import presenter, relations
from app.modules.pikirler.services import thoughts as service

router = APIRouter(prefix="/thoughts", tags=["thoughts"], responses=ERROR_RESPONSES)

Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ThoughtOut, status_code=status.HTTP_201_CREATED)
async def create_thought(body: ThoughtCreate, principal: WriteThoughts, db: DB) -> ThoughtOut:
    thought = await service.create(db, principal, body)
    return await presenter.present_one(db, thought)


@router.get("", response_model=Page[ThoughtOut])
async def list_thoughts(
    principal: ReadThoughts,
    db: DB,
    limit: Limit = 30,
    cursor: str | None = None,
    type: ThoughtType | None = None,
    status: ThoughtStatus | None = None,
    tag: str | None = Query(default=None, max_length=64),
    project_id: uuid.UUID | None = None,
    bookmarked: bool | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    deleted: bool = Query(default=False, description="true lists the trash"),
) -> Page[ThoughtOut]:
    filters = ThoughtFilters(
        thought_type=type.value if type else None,
        status=status.value if status else None,
        tag=tag,
        project_id=project_id,
        bookmarked=bookmarked,
        date_from=date_from,
        date_to=date_to,
        deleted=deleted,
    )
    rows, next_cursor = await repo.page(db, principal.owner_id, filters, limit, cursor)
    return Page(items=await presenter.present(db, rows), next_cursor=next_cursor)


@router.get("/{thought_id}", response_model=ThoughtOut)
async def get_thought(
    thought_id: uuid.UUID, principal: ReadThoughts, db: DB, include_deleted: bool = False
) -> ThoughtOut:
    thought = await repo.get(
        db, principal.owner_id, thought_id, include_deleted=include_deleted, load=True
    )
    return await presenter.present_one(db, thought)


@router.patch(
    "/{thought_id}",
    response_model=ThoughtOut,
    responses={409: {"description": "expected_version is stale"}},
)
async def update_thought(
    thought_id: uuid.UUID, body: ThoughtUpdate, principal: WriteThoughts, db: DB
) -> ThoughtOut:
    thought = await service.update_thought(db, principal, thought_id, body)
    return await presenter.present_one(db, thought)


@router.delete("/{thought_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thought(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> Response:
    await service.soft_delete(db, principal.owner_id, thought_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{thought_id}/restore", response_model=ThoughtOut)
async def restore_thought(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> ThoughtOut:
    thought = await service.restore(db, principal.owner_id, thought_id)
    return await presenter.present_one(db, thought)


async def _flag(
    db: DB, owner_id: uuid.UUID, thought_id: uuid.UUID, flag: str, value: bool
) -> ThoughtOut:
    thought = await service.set_flag(db, owner_id, thought_id, flag, value)
    return await presenter.present_one(db, thought)


@router.post("/{thought_id}/bookmark", response_model=ThoughtOut)
async def bookmark(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> ThoughtOut:
    return await _flag(db, principal.owner_id, thought_id, "is_bookmarked", True)


@router.delete("/{thought_id}/bookmark", response_model=ThoughtOut)
async def unbookmark(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> ThoughtOut:
    return await _flag(db, principal.owner_id, thought_id, "is_bookmarked", False)


@router.post("/{thought_id}/pin", response_model=ThoughtOut)
async def pin(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> ThoughtOut:
    return await _flag(db, principal.owner_id, thought_id, "is_pinned", True)


@router.delete("/{thought_id}/pin", response_model=ThoughtOut)
async def unpin(thought_id: uuid.UUID, principal: WriteThoughts, db: DB) -> ThoughtOut:
    return await _flag(db, principal.owner_id, thought_id, "is_pinned", False)


@router.get("/{thought_id}/revisions", response_model=list[RevisionOut])
async def revisions(thought_id: uuid.UUID, principal: ReadThoughts, db: DB) -> list[RevisionOut]:
    rows = await service.list_revisions(db, principal.owner_id, thought_id)
    return [RevisionOut.model_validate(r) for r in rows]


@router.post("/{thought_id}/revisions/{revision_id}/restore", response_model=ThoughtOut)
async def restore_revision(
    thought_id: uuid.UUID,
    revision_id: uuid.UUID,
    principal: WriteThoughts,
    db: DB,
    body: RevisionRestore | None = None,
) -> ThoughtOut:
    thought = await service.restore_revision(
        db, principal, thought_id, revision_id, body.expected_version if body else None
    )
    return await presenter.present_one(db, thought)


@router.post(
    "/{thought_id}/replies",
    response_model=ThoughtOut,
    status_code=status.HTTP_201_CREATED,
    tags=["threads"],
)
async def reply(
    thought_id: uuid.UUID, body: ReplyCreate, principal: WriteThoughts, db: DB
) -> ThoughtOut:
    thought = await service.reply(db, principal, thought_id, body)
    return await presenter.present_one(db, thought)


@router.get("/{thought_id}/relations", response_model=list[RelationOut], tags=["relations"])
async def list_relations(
    thought_id: uuid.UUID,
    principal: ReadThoughts,
    db: DB,
    status: str | None = Query(default=None, pattern="^(suggested|confirmed|rejected)$"),
) -> list[RelationOut]:
    rows = await relations.list_for_thought(db, principal.owner_id, thought_id, status)
    return [
        RelationOut.model_validate(r).model_copy(update={"ai_generated": r.created_by == "ai"})
        for r in rows
    ]


@router.post(
    "/{thought_id}/relations",
    response_model=RelationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["relations"],
)
async def create_relation(
    thought_id: uuid.UUID, body: RelationCreate, principal: WriteThoughts, db: DB
) -> RelationOut:
    rel = await relations.create(
        db, principal.owner_id, thought_id, body.target_thought_id, body.relation_type.value
    )
    return RelationOut.model_validate(rel).model_copy(
        update={"ai_generated": rel.created_by == "ai"}
    )
