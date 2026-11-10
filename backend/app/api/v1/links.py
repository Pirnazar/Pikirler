"""Saved copies of linked web pages."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response

from app.api.deps import DB, AIAccess, MediaRead, ReadThoughts, WriteThoughts
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.links import (
    LinkDetail,
    LinkOut,
    LinkSummary,
    MediaHealth,
    SummaryRequest,
)
from app.modules.pikirler.services import link_summary, links

router = APIRouter(prefix="/links", tags=["links"], responses=ERROR_RESPONSES)


@router.get("/media-health", response_model=MediaHealth)
async def media_health(principal: ReadThoughts, db: DB) -> MediaHealth:
    """Is the video downloader still working? (yt-dlp version, recent downloads)"""
    return MediaHealth.model_validate(await links.media_health(db, principal.owner_id))


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


@router.post("/{link_id}/seen", response_model=LinkOut)
async def mark_seen(link_id: uuid.UUID, principal: WriteThoughts, db: DB) -> LinkOut:
    """The owner looked at the copy: its comments stop being "new"."""
    return await links.mark_seen(db, principal.owner_id, link_id)


@router.post("/{link_id}/summary", response_model=LinkSummary)
async def summarize_link(
    link_id: uuid.UUID, principal: AIAccess, db: DB, body: SummaryRequest | None = None
) -> LinkSummary:
    """A short AI summary of the saved text (needs the AI to be set up and,
    for an external provider, the owner's consent). Kept and reused."""
    req = body or SummaryRequest()
    return await link_summary.summarize(db, principal, link_id, req.language, req.refresh)
