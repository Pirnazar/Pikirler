from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import DB, WriteThoughts
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.organize import RelationOut
from app.modules.pikirler.services import relations as service

router = APIRouter(prefix="/relations", tags=["relations"], responses=ERROR_RESPONSES)


def _out(rel: object) -> RelationOut:
    out = RelationOut.model_validate(rel)
    return out.model_copy(update={"ai_generated": out.created_by == "ai"})


@router.post("/{relation_id}/confirm", response_model=RelationOut)
async def confirm(relation_id: uuid.UUID, principal: WriteThoughts, db: DB) -> RelationOut:
    """The owner accepts a (usually AI-suggested) relation."""
    return _out(await service.decide(db, principal.owner_id, relation_id, confirm=True))


@router.post("/{relation_id}/reject", response_model=RelationOut)
async def reject(relation_id: uuid.UUID, principal: WriteThoughts, db: DB) -> RelationOut:
    return _out(await service.decide(db, principal.owner_id, relation_id, confirm=False))


@router.delete("/{relation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(relation_id: uuid.UUID, principal: WriteThoughts, db: DB) -> Response:
    await service.delete(db, principal.owner_id, relation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
