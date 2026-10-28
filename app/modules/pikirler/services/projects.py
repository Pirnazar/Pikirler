"""Projects that thoughts can be filed under (not a task manager)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import Conflict, NotFound
from app.modules.pikirler.models import Project, Thought, ThoughtProject


async def _counts(
    db: AsyncSession, owner_id: uuid.UUID, ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = await db.execute(
        select(ThoughtProject.project_id, func.count())
        .join(Thought, Thought.id == ThoughtProject.thought_id)
        .where(
            ThoughtProject.owner_id == owner_id,
            ThoughtProject.project_id.in_(ids),
            Thought.deleted_at.is_(None),
        )
        .group_by(ThoughtProject.project_id)
    )
    return {pid: int(n) for pid, n in rows.all()}


async def list_projects(
    db: AsyncSession, owner_id: uuid.UUID, status: str | None
) -> list[tuple[Project, int]]:
    stmt = select(Project).where(Project.owner_id == owner_id)
    if status:
        stmt = stmt.where(Project.status == status)
    projects = list((await db.execute(stmt.order_by(func.lower(Project.name)))).scalars())
    counts = await _counts(db, owner_id, [p.id for p in projects])
    return [(p, counts.get(p.id, 0)) for p in projects]


async def get_project(db: AsyncSession, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    project = (
        await db.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Project not found", code="project_not_found")
    return project


async def thought_count(db: AsyncSession, owner_id: uuid.UUID, project_id: uuid.UUID) -> int:
    return (await _counts(db, owner_id, [project_id])).get(project_id, 0)


async def _commit_unique(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise Conflict(
            "A project with this name or external_ref already exists", code="project_exists"
        ) from exc


async def create_project(
    db: AsyncSession,
    owner_id: uuid.UUID,
    name: str,
    description: str | None,
    status: str,
    external_ref: str | None,
) -> Project:
    project = Project(
        owner_id=owner_id,
        name=name.strip(),
        description=description,
        status=status,
        external_ref=external_ref,
    )
    db.add(project)
    await _commit_unique(db)
    return project


async def update_project(
    db: AsyncSession, owner_id: uuid.UUID, project_id: uuid.UUID, changes: dict[str, object]
) -> Project:
    project = await get_project(db, owner_id, project_id)
    for key, value in changes.items():
        setattr(project, key, value.strip() if key == "name" and isinstance(value, str) else value)
    await _commit_unique(db)
    await db.refresh(project)
    return project


async def delete_project(db: AsyncSession, owner_id: uuid.UUID, project_id: uuid.UUID) -> None:
    """Deletes the project and its links; the thoughts themselves stay."""
    project = await get_project(db, owner_id, project_id)
    await db.delete(project)
    await db.commit()
