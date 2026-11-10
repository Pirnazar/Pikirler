"""Readable copies of web pages linked from a thought."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.pikirler.models.enums import LinkStatus, check_in
from app.modules.pikirler.models.thought import _owned_fk


class LinkSnapshot(Base):
    """One link of one thought, fetched once: title, author, text and cover are
    kept here, so the thought stays readable when the page changes or goes."""

    __tablename__ = "link_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="article")
    error_code: Mapped[str | None] = mapped_column(String(64))
    site_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    author_username: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str | None] = mapped_column(Text)
    content_text: Mapped[str | None] = mapped_column(Text)
    word_count: Mapped[int | None] = mapped_column(Integer)
    cover_key: Mapped[str | None] = mapped_column(Text)
    # Comments (Teswirler only), as a tree kept in step with the site:
    # [{id, author, username, text, quote, created_at, likes, image_url,
    #   deleted_at, replies: [...]}]; ``deleted_at`` marks one gone on the site.
    comments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    comment_count: Mapped[int | None] = mapped_column(Integer)
    deleted_comment_count: Mapped[int | None] = mapped_column(Integer)
    # Background sync with the original (Teswirler): when it last ran, and when
    # the original was found gone (the copy is kept).
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("thought_id", "url", name="uq_link_snapshots_thought_url"),
        _owned_fk("thought_id", "thoughts"),
        CheckConstraint(check_in("status", LinkStatus), name="status"),
        Index("ix_link_snapshots_owner", "owner_id", "created_at"),
    )
