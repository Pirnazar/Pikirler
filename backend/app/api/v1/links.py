"""Saved copies of linked web pages."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response

from app.api.deps import DB, MediaRead, ReadThoughts, WriteThoughts
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.links import LinkDetail, LinkOut
from app.modules.pikirler.services import links

router = APIRouter(prefix="/links", tags=["links"], responses=ERROR_RESPONSES)


@router.get("/{link_id}", response_model=LinkDetail)
async def get_link(link_id: uuid.UUID, principal: ReadThoughts, db: DB) -> LinkDetail:
    """The saved page with its full text."""
    return await links.detail(db, principal.owner_id, link_id)


@router.post("/{link_id}/refetch", response_model=LinkOut)
async def refetch_link(link_id: uuid.UUID, principal: WriteThoughts, db: DB) -> LinkOut:
    """Fetch the page again (after a failure, or to update the copy)."""
    return await links.refetch(db, principal.owner_id, link_id)


@router.get(
    "/{link_id}/cover",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def link_cover(link_id: uuid.UUID, principal: MediaRead, db: DB) -> Response:
    data = await links.cover_bytes(db, principal.owner_id, link_id)
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
