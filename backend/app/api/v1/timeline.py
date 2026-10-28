"""Home feed and its preset views. Only published thoughts appear here; drafts
and archived thoughts are reachable through ``GET /thoughts?status=``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DB, ReadThoughts
from app.modules.pikirler.models import Thought
from app.modules.pikirler.models.enums import ThoughtStatus, ThoughtType
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.repositories.thoughts import ThoughtFilters
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.schemas.thoughts import ThoughtOut
from app.modules.pikirler.services import presenter

router = APIRouter(prefix="/timeline", tags=["timeline"], responses=ERROR_RESPONSES)

Limit = Annotated[int, Query(ge=1, le=100)]


class TimelinePage(Schema):
    pinned: list[ThoughtOut]
    items: list[ThoughtOut]
    next_cursor: str | None


async def _timeline(
    db: DB,
    principal: ReadThoughts,
    filters: ThoughtFilters,
    limit: int,
    cursor: str | None,
    with_pinned: bool = False,
) -> TimelinePage:
    filters.status = ThoughtStatus.published.value
    rows, next_cursor = await repo.page(db, principal.owner_id, filters, limit, cursor)
    pinned_rows: list[Thought] = []
    if with_pinned and cursor is None:
        pinned_rows, _ = await repo.page(
            db,
            principal.owner_id,
            ThoughtFilters(status=ThoughtStatus.published.value, pinned=True),
            20,
            None,
        )
    items = await presenter.present(db, pinned_rows + rows)
    return TimelinePage(
        pinned=items[: len(pinned_rows)], items=items[len(pinned_rows) :], next_cursor=next_cursor
    )


@router.get("", response_model=TimelinePage)
async def home(
    principal: ReadThoughts, db: DB, limit: Limit = 30, cursor: str | None = None
) -> TimelinePage:
    """Pinned thoughts (first page only) plus the chronological feed."""
    return await _timeline(db, principal, ThoughtFilters(), limit, cursor, with_pinned=True)


@router.get("/bookmarked", response_model=TimelinePage)
async def bookmarked(
    principal: ReadThoughts, db: DB, limit: Limit = 30, cursor: str | None = None
) -> TimelinePage:
    return await _timeline(db, principal, ThoughtFilters(bookmarked=True), limit, cursor)


@router.get("/media", response_model=TimelinePage)
async def media(
    principal: ReadThoughts, db: DB, limit: Limit = 30, cursor: str | None = None
) -> TimelinePage:
    return await _timeline(db, principal, ThoughtFilters(has_media=True), limit, cursor)


@router.get("/ideas", response_model=TimelinePage)
async def ideas(
    principal: ReadThoughts, db: DB, limit: Limit = 30, cursor: str | None = None
) -> TimelinePage:
    return await _timeline(
        db, principal, ThoughtFilters(thought_type=ThoughtType.idea.value), limit, cursor
    )


@router.get("/projects", response_model=TimelinePage)
async def projects(
    principal: ReadThoughts, db: DB, limit: Limit = 30, cursor: str | None = None
) -> TimelinePage:
    """Thoughts filed under at least one project."""
    return await _timeline(db, principal, ThoughtFilters(in_any_project=True), limit, cursor)
