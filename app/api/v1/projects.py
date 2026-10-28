from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response, status

from app.api.deps import DB, ReadThoughts, WriteThoughts
from app.modules.pikirler.models import Project
from app.modules.pikirler.models.enums import ProjectStatus
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.organize import ProjectCreate, ProjectOut, ProjectUpdate
from app.modules.pikirler.services import projects as service

router = APIRouter(prefix="/projects", tags=["projects"], responses=ERROR_RESPONSES)


def _out(project: Project, count: int) -> ProjectOut:
    return ProjectOut.model_validate(project).model_copy(update={"thought_count": count})


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    principal: ReadThoughts, db: DB, status: ProjectStatus | None = Query(default=None)
) -> list[ProjectOut]:
    rows = await service.list_projects(db, principal.owner_id, status.value if status else None)
    return [_out(p, n) for p, n in rows]


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, principal: WriteThoughts, db: DB) -> ProjectOut:
    project = await service.create_project(
        db, principal.owner_id, body.name, body.description, body.status.value, body.external_ref
    )
    return _out(project, 0)


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, principal: ReadThoughts, db: DB) -> ProjectOut:
    project = await service.get_project(db, principal.owner_id, project_id)
    return _out(project, await service.thought_count(db, principal.owner_id, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, principal: WriteThoughts, db: DB
) -> ProjectOut:
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is not None:
        changes["status"] = changes["status"].value
    project = await service.update_project(db, principal.owner_id, project_id, changes)
    return _out(project, await service.thought_count(db, principal.owner_id, project_id))


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, principal: WriteThoughts, db: DB) -> Response:
    await service.delete_project(db, principal.owner_id, project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
