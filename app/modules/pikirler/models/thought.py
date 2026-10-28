from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.pikirler.models.enums import (
    ContentFormat,
    ProjectStatus,
    RelationCreator,
    RelationStatus,
    RelationType,
    RevisionSource,
    TagOrigin,
    ThoughtSource,
    ThoughtStatus,
    ThoughtType,
    Visibility,
    check_in,
)


def _owned_fk(column: str, table: str) -> ForeignKeyConstraint:
    """(column, owner_id) -> table(id, owner_id): same-owner links only."""
    return ForeignKeyConstraint(
        [column, "owner_id"],
        [f"{table}.id", f"{table}.owner_id"],
        ondelete="CASCADE",
        name=f"fk_{column}_owner_{table}",
    )


SEARCH_VECTOR_SQL = (
    "to_tsvector('simple'::regconfig, coalesce(search_document, '')) || "
    "to_tsvector('russian'::regconfig, coalesce(search_document, ''))"
)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    root_thought_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "thoughts.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_threads_root_thought_id_thoughts",
        ),
    )
    title: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_threads_owner_activity", "owner_id", text("last_activity_at DESC")),
    )


class Thought(Base):
    __tablename__ = "thoughts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    content_format: Mapped[str] = mapped_column(String(16), nullable=False, server_default="plain")
    thought_type: Mapped[str] = mapped_column(String(16), nullable=False, server_default="thought")
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, server_default="private")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="published")
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="web")
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("thoughts.id", ondelete="RESTRICT")
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL")
    )
    is_bookmarked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    search_document: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True)
    )

    tag_links: Mapped[list[ThoughtTag]] = relationship(
        back_populates="thought",
        cascade="all, delete-orphan",
        lazy="raise",
        primaryjoin="Thought.id == foreign(ThoughtTag.thought_id)",
    )
    project_links: Mapped[list[ThoughtProject]] = relationship(
        back_populates="thought",
        cascade="all, delete-orphan",
        lazy="raise",
        primaryjoin="Thought.id == foreign(ThoughtProject.thought_id)",
    )
    attachment_links: Mapped[list[Any]] = relationship(
        "ThoughtAttachment",
        back_populates="thought",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="ThoughtAttachment.position",
        primaryjoin="Thought.id == foreign(ThoughtAttachment.thought_id)",
    )

    __table_args__ = (
        # Target of the composite (id, owner_id) foreign keys below: a link row can
        # never join a thought, tag, project or attachment of another owner.
        UniqueConstraint("id", "owner_id", name="uq_thoughts_id_owner"),
        CheckConstraint(check_in("thought_type", ThoughtType), name="thought_type"),
        CheckConstraint(check_in("status", ThoughtStatus), name="status"),
        CheckConstraint(check_in("content_format", ContentFormat), name="content_format"),
        CheckConstraint(check_in("visibility", Visibility), name="visibility_private"),
        CheckConstraint(check_in("source", ThoughtSource), name="source"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index(
            "ix_thoughts_feed",
            "owner_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_thoughts_status_feed",
            "owner_id",
            "status",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_thoughts_type_feed",
            "owner_id",
            "thought_type",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_thoughts_bookmarked",
            "owner_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL AND is_bookmarked"),
        ),
        Index(
            "ix_thoughts_pinned",
            "owner_id",
            postgresql_where=text("deleted_at IS NULL AND is_pinned"),
        ),
        Index(
            "ix_thoughts_trash",
            "owner_id",
            text("deleted_at DESC"),
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
        Index("ix_thoughts_thread", "thread_id", "created_at"),
        Index("ix_thoughts_parent", "parent_id"),
        Index("ix_thoughts_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_thoughts_search_trgm",
            "search_document",
            postgresql_using="gin",
            postgresql_ops={"search_document": "gin_trgm_ops"},
        ),
    )


class ThoughtRevision(Base):
    __tablename__ = "thought_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_content: Mapped[str | None] = mapped_column(Text)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    change_source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("thought_id", "revision_number", name="uq_thought_revisions_number"),
        _owned_fk("thought_id", "thoughts"),
        CheckConstraint(check_in("change_source", RevisionSource), name="change_source"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_name", name="uq_tags_owner_normalized"),
        UniqueConstraint("id", "owner_id", name="uq_tags_id_owner"),
        Index(
            "ix_tags_prefix",
            "owner_id",
            "normalized_name",
            postgresql_ops={"normalized_name": "text_pattern_ops"},
        ),
    )


class ThoughtTag(Base):
    __tablename__ = "thought_tags"

    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thought: Mapped[Thought] = relationship(
        back_populates="tag_links",
        lazy="raise",
        primaryjoin="Thought.id == foreign(ThoughtTag.thought_id)",
    )
    tag: Mapped[Tag] = relationship(
        lazy="raise", primaryjoin="Tag.id == foreign(ThoughtTag.tag_id)"
    )

    __table_args__ = (
        _owned_fk("thought_id", "thoughts"),
        _owned_fk("tag_id", "tags"),
        Index("ix_thought_tags_tag", "tag_id"),
        CheckConstraint(check_in("origin", TagOrigin), name="origin"),
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    # Stable identifier of the matching Second Brain project, when linked.
    external_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("uq_projects_owner_name", "owner_id", text("lower(name)"), unique=True),
        UniqueConstraint("id", "owner_id", name="uq_projects_id_owner"),
        Index(
            "uq_projects_owner_external_ref",
            "owner_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
        ),
        CheckConstraint(check_in("status", ProjectStatus), name="status"),
    )


class ThoughtProject(Base):
    __tablename__ = "thought_projects"

    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thought: Mapped[Thought] = relationship(
        back_populates="project_links",
        lazy="raise",
        primaryjoin="Thought.id == foreign(ThoughtProject.thought_id)",
    )
    project: Mapped[Project] = relationship(
        lazy="raise", primaryjoin="Project.id == foreign(ThoughtProject.project_id)"
    )

    __table_args__ = (
        _owned_fk("thought_id", "thoughts"),
        _owned_fk("project_id", "projects"),
        Index("ix_thought_projects_project", "project_id"),
    )


class ThoughtRelation(Base):
    __tablename__ = "thought_relations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    source_thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[str] = mapped_column(String(8), nullable=False, server_default="user")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="confirmed")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "source_thought_id",
            "target_thought_id",
            "relation_type",
            name="uq_thought_relations_edge",
        ),
        _owned_fk("source_thought_id", "thoughts"),
        _owned_fk("target_thought_id", "thoughts"),
        CheckConstraint("source_thought_id <> target_thought_id", name="not_self"),
        CheckConstraint(check_in("relation_type", RelationType), name="relation_type"),
        CheckConstraint(check_in("created_by", RelationCreator), name="created_by"),
        CheckConstraint(check_in("status", RelationStatus), name="status"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint(
            "created_by = 'user' OR status <> 'confirmed' OR confirmed_at IS NOT NULL",
            name="ai_confirmation_explicit",
        ),
        Index("ix_thought_relations_source", "source_thought_id"),
        Index("ix_thought_relations_target", "target_thought_id"),
    )
