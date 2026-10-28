from __future__ import annotations

import uuid
from datetime import date
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query

from app.api.deps import DB, MemoriesRead
from app.modules.pikirler.models.enums import ThoughtType
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.schemas.organize import RelationOut
from app.modules.pikirler.schemas.thoughts import ThoughtOut
from app.modules.pikirler.services import memories as service
from app.modules.pikirler.services import presenter

router = APIRouter(prefix="/memories", tags=["memories"], responses=ERROR_RESPONSES)


class YearGroup(Schema):
    year: int
    years_ago: int
    thoughts: list[ThoughtOut]


class OnThisDay(Schema):
    date: date
    timezone: str
    groups: list[YearGroup]


class RandomMemory(Schema):
    thought: ThoughtOut | None


class TagCount(Schema):
    name: str
    count: int


class DayCount(Schema):
    date: date
    count: int


class MonthlyRecap(Schema):
    year: int
    month: int
    timezone: str
    total: int
    by_type: dict[str, int]
    busiest_day: DayCount | None
    top_tags: list[TagCount]
    highlights: list[ThoughtOut]


class Activity(Schema):
    year: int
    timezone: str
    total: int
    days: list[DayCount]


class Evolution(Schema):
    thought_id: uuid.UUID
    thoughts: list[ThoughtOut]
    relations: list[RelationOut]


@router.get("/on-this-day", response_model=OnThisDay)
async def on_this_day(
    principal: MemoriesRead,
    db: DB,
    day: date | None = Query(
        default=None, alias="date", description="Defaults to today (owner timezone)"
    ),
) -> OnThisDay:
    tz = principal.timezone
    target, rows = await service.on_this_day(db, principal.owner_id, tz, day)
    presented = await presenter.present(db, rows)
    groups: dict[int, list[ThoughtOut]] = {}
    for item in presented:
        groups.setdefault(item.created_at.astimezone(ZoneInfo(tz)).year, []).append(item)
    return OnThisDay(
        date=target,
        timezone=tz,
        groups=[
            YearGroup(year=y, years_ago=target.year - y, thoughts=items)
            for y, items in sorted(groups.items(), reverse=True)
        ],
    )


@router.get("/random", response_model=RandomMemory)
async def random_memory(
    principal: MemoriesRead, db: DB, type: ThoughtType | None = None
) -> RandomMemory:
    thought = await service.random_memory(db, principal.owner_id, type.value if type else None)
    return RandomMemory(thought=await presenter.present_one(db, thought) if thought else None)


@router.get("/monthly", response_model=MonthlyRecap)
async def monthly(
    principal: MemoriesRead,
    db: DB,
    year: int = Query(ge=1970, le=9999),
    month: int = Query(ge=1, le=12),
) -> MonthlyRecap:
    data = await service.monthly(db, principal.owner_id, principal.timezone, year, month)
    data["highlights"] = await presenter.present(db, data["highlights"])
    return MonthlyRecap.model_validate(data)


@router.get("/activity", response_model=Activity)
async def activity(
    principal: MemoriesRead, db: DB, year: int = Query(ge=1970, le=9999)
) -> Activity:
    days = await service.activity(db, principal.owner_id, principal.timezone, year)
    return Activity(
        year=year,
        timezone=principal.timezone,
        total=sum(d["count"] for d in days),
        days=[DayCount.model_validate(d) for d in days],
    )


@router.get("/evolution/{thought_id}", response_model=Evolution)
async def evolution(thought_id: uuid.UUID, principal: MemoriesRead, db: DB) -> Evolution:
    thoughts, relations = await service.evolution(db, principal.owner_id, thought_id)
    return Evolution(
        thought_id=thought_id,
        thoughts=await presenter.present(db, thoughts),
        relations=[
            RelationOut.model_validate(r).model_copy(update={"ai_generated": r.created_by == "ai"})
            for r in relations
        ],
    )


@router.get("/forgotten", response_model=list[ThoughtOut])
async def forgotten(
    principal: MemoriesRead,
    db: DB,
    older_than_days: int = Query(default=180, ge=7, le=3650),
    limit: int = Query(default=5, ge=1, le=50),
) -> list[ThoughtOut]:
    """Ideas never continued, related or edited for a long time."""
    rows = await service.forgotten(db, principal.owner_id, older_than_days, limit)
    return await presenter.present(db, rows)
