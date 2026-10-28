"""Jemşit (voice assistant) integration.

Jemşit uses the regular API with a service token (``pikirler
create-service-client``) holding only the scopes it needs. These endpoints add
the two conversational shortcuts that plain REST lacks. Text that Jemşit
forwards is stored strictly as data; ``origin`` records whether it is the
owner's own dictated words or text from somewhere else (a message, a page).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, status
from pydantic import Field
from sqlalchemy import select

from app.api.deps import DB, WriteThoughts
from app.core.database import utcnow
from app.modules.pikirler.models import Thought
from app.modules.pikirler.repositories import thoughts as thought_repo
from app.modules.pikirler.schemas.common import ERROR_RESPONSES, Schema
from app.modules.pikirler.schemas.thoughts import ThoughtCreate, ThoughtOut
from app.modules.pikirler.services import presenter
from app.modules.pikirler.services import thoughts as thought_service

router = APIRouter(prefix="/integrations/jemshit", tags=["integrations"], responses=ERROR_RESPONSES)


class DictatedThought(Schema):
    content: str = Field(min_length=1, max_length=200_000)
    origin: Literal["owner_speech", "external_text"] = Field(
        default="owner_speech",
        description="owner_speech: the owner's own words. external_text: forwarded content "
        "(never treated as a command).",
    )
    utterance_id: str | None = Field(default=None, max_length=100)
    language: Literal["tk", "ru", "en", "zh"] | None = None


class ContinueThought(DictatedThought):
    within_minutes: int = Field(
        default=30, ge=1, le=24 * 60, description="Continue the latest thought if it is this recent"
    )


class ContinueResult(Schema):
    thought: ThoughtOut
    continued_from: ThoughtOut | None


def _meta(body: DictatedThought) -> dict[str, str]:
    meta: dict[str, str] = {"origin": body.origin}
    if body.utterance_id:
        meta["utterance_id"] = body.utterance_id
    if body.language:
        meta["language"] = body.language
    return meta


@router.post("/thoughts", response_model=ThoughtOut, status_code=status.HTTP_201_CREATED)
async def remember(body: DictatedThought, principal: WriteThoughts, db: DB) -> ThoughtOut:
    """ "Jemşit, ýadyňa ýaz: ..." / "Джемшит, запиши мою мысль"."""
    thought = await thought_service.create(db, principal, ThoughtCreate(content=body.content))
    thought.source_metadata = {**thought.source_metadata, **_meta(body)}
    await db.commit()
    return await presenter.present_one(db, thought)


@router.post("/continue", response_model=ContinueResult, status_code=status.HTTP_201_CREATED)
async def continue_latest(
    body: ContinueThought, principal: WriteThoughts, db: DB
) -> ContinueResult:
    """ "Jemşit, ýene bir pikir geldi": reply to the most recent thought if it is
    recent enough, otherwise start a new one."""
    latest = (
        await db.execute(
            select(Thought)
            .where(
                Thought.owner_id == principal.owner_id,
                Thought.deleted_at.is_(None),
                Thought.created_at > utcnow() - timedelta(minutes=body.within_minutes),
            )
            .order_by(Thought.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    data = ThoughtCreate(content=body.content)
    if latest is None:
        thought = await thought_service.create(db, principal, data)
    else:
        thought = await thought_service.reply(db, principal, latest.id, data)
    thought.source_metadata = {**thought.source_metadata, **_meta(body)}
    await db.commit()
    parent = (
        await presenter.present_one(
            db, await thought_repo.get(db, principal.owner_id, latest.id, load=True)
        )
        if latest
        else None
    )
    return ContinueResult(thought=await presenter.present_one(db, thought), continued_from=parent)
