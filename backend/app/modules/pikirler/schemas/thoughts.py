from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.modules.pikirler.models.enums import ContentFormat, ThoughtStatus, ThoughtType
from app.modules.pikirler.schemas.common import Schema

MAX_CONTENT_CHARS = 200_000
MAX_TAGS = 30
MAX_PROJECTS = 20
MAX_ATTACHMENTS = 50

UserSource = Literal["web", "mobile", "api"]


class TagRef(Schema):
    id: uuid.UUID
    name: str
    normalized_name: str


class ProjectRef(Schema):
    id: uuid.UUID
    name: str
    status: str


class TranscriptionRef(Schema):
    id: uuid.UUID
    status: str
    text: str | None
    language: str | None


class AttachmentRef(Schema):
    id: uuid.UUID
    kind: str
    mime_type: str
    original_filename: str
    file_size: int
    processing_status: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    has_thumbnail: bool = False
    content_url: str
    thumbnail_url: str | None = None
    transcription: TranscriptionRef | None = None


class ThoughtCreate(Schema):
    content: str = Field(default="", max_length=MAX_CONTENT_CHARS)
    content_format: ContentFormat = ContentFormat.plain
    thought_type: ThoughtType = ThoughtType.thought
    status: ThoughtStatus = ThoughtStatus.published
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    project_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_PROJECTS)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_ATTACHMENTS)
    occurred_at: datetime | None = None
    is_bookmarked: bool = False
    is_pinned: bool = False
    source: UserSource | None = Field(
        default=None, description="Client kind. Ignored for service tokens (fixed per client)."
    )

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not tag.strip().lstrip("#").strip() or len(tag) > 64:
                raise ValueError("tags must be 1-64 characters")
        return value


class ReplyCreate(ThoughtCreate):
    """A continuation of an existing thought; joins (or starts) its thread."""


class ThoughtUpdate(Schema):
    """Partial update. ``expected_version`` is required: optimistic locking."""

    expected_version: int = Field(ge=1)
    content: str | None = Field(default=None, max_length=MAX_CONTENT_CHARS)
    content_format: ContentFormat | None = None
    thought_type: ThoughtType | None = None
    status: ThoughtStatus | None = None
    tags: list[str] | None = Field(default=None, max_length=MAX_TAGS)
    project_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_PROJECTS)
    attachment_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_ATTACHMENTS)
    occurred_at: datetime | None = None
    clear_occurred_at: bool = False


class ThoughtOut(Schema):
    id: uuid.UUID
    content: str
    content_format: str
    thought_type: str
    status: str
    visibility: str
    source: str
    parent_id: uuid.UUID | None
    thread_id: uuid.UUID | None
    is_bookmarked: bool
    is_pinned: bool
    occurred_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    version: int
    tags: list[str]
    tag_refs: list[TagRef]
    projects: list[ProjectRef]
    attachments: list[AttachmentRef]
    reply_count: int = 0


class RevisionOut(Schema):
    id: uuid.UUID
    thought_id: uuid.UUID
    revision_number: int
    previous_content: str | None
    new_content: str
    change_source: str
    created_at: datetime


class RevisionRestore(Schema):
    expected_version: int | None = Field(
        default=None, ge=1, description="When given, the restore fails on a concurrent edit."
    )
