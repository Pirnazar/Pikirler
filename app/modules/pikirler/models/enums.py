"""Value sets stored as VARCHAR + CHECK constraints.

CHECK constraints instead of native PostgreSQL ENUM types: adding a value is a
one-line migration and never needs ``ALTER TYPE`` outside a transaction.
"""

from __future__ import annotations

from enum import StrEnum


class ThoughtType(StrEnum):
    thought = "thought"
    idea = "idea"
    reflection = "reflection"
    journal = "journal"
    project = "project"


class ThoughtStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class ContentFormat(StrEnum):
    plain = "plain"
    markdown = "markdown"


class Visibility(StrEnum):
    private = "private"


class ThoughtSource(StrEnum):
    web = "web"
    mobile = "mobile"
    telegram = "telegram"
    jemshit = "jemshit"
    api = "api"
    import_ = "import"


class RevisionSource(StrEnum):
    create = "create"
    edit = "edit"
    restore = "restore"
    import_ = "import"


class TagOrigin(StrEnum):
    manual = "manual"
    hashtag = "hashtag"


class ProjectStatus(StrEnum):
    active = "active"
    paused = "paused"
    completed = "completed"
    archived = "archived"


class RelationType(StrEnum):
    related = "related"
    continues = "continues"
    contradicts = "contradicts"
    supports = "supports"
    inspired_by = "inspired_by"
    same_topic = "same_topic"


class RelationStatus(StrEnum):
    suggested = "suggested"
    confirmed = "confirmed"
    rejected = "rejected"


class RelationCreator(StrEnum):
    user = "user"
    ai = "ai"


class AttachmentKind(StrEnum):
    image = "image"
    video = "video"
    audio = "audio"
    document = "document"
    other = "other"


class ProcessingStatus(StrEnum):
    pending_upload = "pending_upload"
    uploaded = "uploaded"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class TranscriptionStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class JobStatus(StrEnum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"  # will be retried
    dead = "dead"  # out of attempts; manual retry only


def check_in(column: str, enum: type[StrEnum]) -> str:
    values = ", ".join(f"'{v.value}'" for v in enum)
    return f"{column} IN ({values})"
