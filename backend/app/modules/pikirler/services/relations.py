"""Typed links between thoughts. AI may only *suggest*; confirmation is a
separate explicit action of the owner."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import utcnow
from app.core.exceptions import Conflict, NotFound, ValidationFailed
from app.modules.pikirler.models import ThoughtRelation
from app.modules.pikirler.models.enums import RelationCreator, RelationStatus
from app.modules.pikirler.repositories import thoughts as repo


async def list_for_thought(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID, status: str | None
) -> list[ThoughtRelation]:
    await repo.get(db, owner_id, thought_id)
    stmt = select(ThoughtRelation).where(
        ThoughtRelation.owner_id == owner_id,
        or_(
            ThoughtRelation.source_thought_id == thought_id,
            ThoughtRelation.target_thought_id == thought_id,
        ),
    )
    if status:
        stmt = stmt.where(ThoughtRelation.status == status)
    else:
        stmt = stmt.where(ThoughtRelation.status != RelationStatus.rejected.value)
    return list((await db.execute(stmt.order_by(ThoughtRelation.created_at.desc()))).scalars())


async def create(
    db: AsyncSession,
    owner_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation_type: str,
) -> ThoughtRelation:
    if source_id == target_id:
        raise ValidationFailed("A thought cannot relate to itself", code="self_relation")
    if len(await repo.owned_ids(db, owner_id, [source_id, target_id])) != 2:
        raise NotFound("Thought not found", code="thought_not_found")
    existing = (
        await db.execute(
            select(ThoughtRelation).where(
                ThoughtRelation.owner_id == owner_id,
                ThoughtRelation.source_thought_id == source_id,
                ThoughtRelation.target_thought_id == target_id,
                ThoughtRelation.relation_type == relation_type,
            )
        )
    ).scalar_one_or_none()
    now = utcnow()
    if existing is not None:
        if existing.status == RelationStatus.confirmed.value:
            raise Conflict(
                "Relation already exists",
                code="relation_exists",
                details={"relation_id": str(existing.id)},
            )
        existing.status = RelationStatus.confirmed.value
        existing.confirmed_at = now
        await db.commit()
        return existing
    relation = ThoughtRelation(
        owner_id=owner_id,
        source_thought_id=source_id,
        target_thought_id=target_id,
        relation_type=relation_type,
        created_by=RelationCreator.user.value,
        status=RelationStatus.confirmed.value,
        confirmed_at=now,
    )
    db.add(relation)
    await db.commit()
    return relation


async def suggest(
    db: AsyncSession,
    owner_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    relation_type: str,
    confidence: float | None,
    rationale: str | None,
) -> None:
    """Record an AI suggestion; never touches a relation the owner already decided on."""
    await db.execute(
        insert(ThoughtRelation)
        .values(
            id=uuid.uuid4(),
            owner_id=owner_id,
            source_thought_id=source_id,
            target_thought_id=target_id,
            relation_type=relation_type,
            created_by=RelationCreator.ai.value,
            status=RelationStatus.suggested.value,
            confidence=Decimal(str(round(confidence, 3))) if confidence is not None else None,
            rationale=rationale,
        )
        .on_conflict_do_nothing(constraint="uq_thought_relations_edge")
    )


async def _get(db: AsyncSession, owner_id: uuid.UUID, relation_id: uuid.UUID) -> ThoughtRelation:
    relation = (
        await db.execute(
            select(ThoughtRelation).where(
                ThoughtRelation.id == relation_id, ThoughtRelation.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()
    if relation is None:
        raise NotFound("Relation not found", code="relation_not_found")
    return relation


async def decide(
    db: AsyncSession, owner_id: uuid.UUID, relation_id: uuid.UUID, confirm: bool
) -> ThoughtRelation:
    relation = await _get(db, owner_id, relation_id)
    relation.status = (RelationStatus.confirmed if confirm else RelationStatus.rejected).value
    relation.confirmed_at = utcnow() if confirm else None
    await db.commit()
    return relation


async def delete(db: AsyncSession, owner_id: uuid.UUID, relation_id: uuid.UUID) -> None:
    relation = await _get(db, owner_id, relation_id)
    await db.delete(relation)
    await db.commit()
