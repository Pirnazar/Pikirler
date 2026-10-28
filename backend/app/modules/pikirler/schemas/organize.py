"""Tags, projects, relations and threads."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.pikirler.models.enums import ProjectStatus, RelationType
from app.modules.pikirler.schemas.common import Schema
from app.modules.pikirler.schemas.thoughts import ThoughtOut


class TagOut(Schema):
    id: uuid.UUID
    name: str
    normalized_name: str
    usage_count: int = 0
    created_at: datetime


class TagCreate(Schema):
    name: str = Field(min_length=1, max_length=64)


class TagUpdate(Schema):
    name: str = Field(min_length=1, max_length=64)


class TagMerge(Schema):
    into_tag_id: uuid.UUID


class ProjectOut(Schema):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    external_ref: str | None
    thought_count: int = 0
    created_at: datetime
    updated_at: datetime


class ProjectCreate(Schema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: ProjectStatus = ProjectStatus.active
    external_ref: str | None = Field(
        default=None, max_length=200, description="Stable id of the Second Brain project"
    )


class ProjectUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: ProjectStatus | None = None
    external_ref: str | None = Field(default=None, max_length=200)


class RelationOut(Schema):
    id: uuid.UUID
    source_thought_id: uuid.UUID
    target_thought_id: uuid.UUID
    relation_type: str
    created_by: str
    status: str
    confidence: float | None
    rationale: str | None
    created_at: datetime
    confirmed_at: datetime | None
    ai_generated: bool = False


class RelationCreate(Schema):
    target_thought_id: uuid.UUID
    relation_type: RelationType = RelationType.related


class ThreadNode(Schema):
    thought: ThoughtOut
    depth: int
    previous_id: uuid.UUID | None
    next_id: uuid.UUID | None


class ThreadOut(Schema):
    id: uuid.UUID
    title: str | None
    root_thought_id: uuid.UUID | None
    created_at: datetime
    last_activity_at: datetime
    thought_count: int
    items: list[ThreadNode] = Field(description="Depth-first order: each reply under its parent")


class ThreadTimeline(Schema):
    id: uuid.UUID
    items: list[ThreadNode] = Field(description="Strictly chronological order")


class ThreadUpdate(Schema):
    title: str | None = Field(default=None, max_length=300)
