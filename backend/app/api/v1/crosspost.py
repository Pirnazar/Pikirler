"""Publishing copies of thoughts on other sites (Teswirler). Owner sessions only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import DB, Account
from app.modules.pikirler.models.enums import CrosspostTarget
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.crosspost import (
    CrosspostOut,
    TeswirlerAccountOut,
    TeswirlerConnect,
)
from app.modules.pikirler.services import crosspost

router = APIRouter(tags=["crosspost"], responses=ERROR_RESPONSES)


@router.get("/crosspost/teswirler", response_model=TeswirlerAccountOut)
async def teswirler_account(principal: Account, db: DB) -> TeswirlerAccountOut:
    return await crosspost.get_account(db, principal.owner_id)


@router.put("/crosspost/teswirler", response_model=TeswirlerAccountOut)
async def connect_teswirler(
    body: TeswirlerConnect, principal: Account, db: DB
) -> TeswirlerAccountOut:
    """Sign in to Teswirler once. The password is not stored."""
    return await crosspost.connect(db, principal, body.identifier, body.password)


@router.delete("/crosspost/teswirler", response_model=TeswirlerAccountOut)
async def disconnect_teswirler(principal: Account, db: DB) -> TeswirlerAccountOut:
    return await crosspost.disconnect(db, principal)


@router.get("/thoughts/{thought_id}/crossposts", response_model=list[CrosspostOut])
async def list_crossposts(thought_id: uuid.UUID, principal: Account, db: DB) -> list[CrosspostOut]:
    return await crosspost.for_thought(db, principal.owner_id, thought_id)


@router.post("/thoughts/{thought_id}/crossposts/{target}", response_model=list[CrosspostOut])
async def share_thought(
    thought_id: uuid.UUID, target: CrosspostTarget, principal: Account, db: DB
) -> list[CrosspostOut]:
    """Share an existing thought, or try again after ``failed``/``unknown``."""
    return await crosspost.share(db, principal, thought_id, target)
