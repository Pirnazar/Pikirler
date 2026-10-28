"""Tags (topics)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, ReadThoughts, WriteThoughts
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.organize import TagCreate, TagMerge, TagOut, TagUpdate
from app.modules.pikirler.services import tags as service

router = APIRouter(prefix="/tags", tags=["tags"], responses=ERROR_RESPONSES)


def _out(tag: object, usage: int) -> TagOut:
    return TagOut.model_validate(tag).model_copy(update={"usage_count": usage})


@router.get("", response_model=list[TagOut])
async def list_tags(
    principal: ReadThoughts,
    db: DB,
    q: str | None = Query(default=None, max_length=64, description="Prefix for autocomplete"),
    sort: str = Query(default="name", pattern="^(name|popular)$"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TagOut]:
    rows = await service.list_tags(db, principal.owner_id, q, sort, limit)
    return [_out(tag, n) for tag, n in rows]


@router.post("", response_model=TagOut, status_code=status.HTTP_201_CREATED)
async def create_tag(body: TagCreate, principal: WriteThoughts, db: DB) -> TagOut:
    tag = await service.create_tag(db, principal.owner_id, body.name)
    return _out(tag, 0)


@router.patch("/{tag_id}", response_model=TagOut)
async def rename_tag(
    tag_id: uuid.UUID, body: TagUpdate, principal: WriteThoughts, db: DB
) -> TagOut:
    tag = await service.rename_tag(db, principal.owner_id, tag_id, body.name)
    return _out(tag, await service.tag_usage(db, principal.owner_id, tag.id))


@router.post("/{tag_id}/merge", response_model=TagOut)
async def merge_tag(tag_id: uuid.UUID, body: TagMerge, principal: WriteThoughts, db: DB) -> TagOut:
    """Move every thought of ``tag_id`` to ``into_tag_id`` and delete ``tag_id``."""
    tag = await service.merge_tags(db, principal.owner_id, tag_id, body.into_tag_id)
    return _out(tag, await service.tag_usage(db, principal.owner_id, tag.id))


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(tag_id: uuid.UUID, principal: WriteThoughts, db: DB) -> Response:
    await service.delete_tag(db, principal.owner_id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
