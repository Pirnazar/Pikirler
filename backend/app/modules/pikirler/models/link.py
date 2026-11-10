"""Readable copies of web pages linked from thoughts: one copy per page,
shared by every thought that links to it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
    """One page (by ``canonical_url``), fetched once and kept in step with the
    site: title, author, text, cover, video, comments. The thoughts that link to
    it are in ``thought_links``; a copy no thought links to is removed."""

    __tablename__ = "link_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    # The page's key (``services.link_keys.canonical_url``) and the link as
    # first written.
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
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
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    # The downloaded video (YouTube, TikTok, Instagram). ``media_status`` is
    # null where nothing is downloaded, else pending / ready / failed.
    media_attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attachments.id", ondelete="SET NULL")
    )
    media_status: Mapped[str | None] = mapped_column(String(16))
    media_error: Mapped[str | None] = mapped_column(String(64))
    media_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cover_key: Mapped[str | None] = mapped_column(Text)
    # Comments (Teswirler only), as a tree kept in step with the site:
    # [{id, author, username, text, quote, created_at, likes, image_url,
    #   deleted_at, replies: [...]}]; ``deleted_at`` marks one gone on the site.
    comments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    comment_count: Mapped[int | None] = mapped_column(Integer)
    deleted_comment_count: Mapped[int | None] = mapped_column(Integer)
    # Comments first seen after this moment are "new" (the owner has not read
    # them); opening the copy moves it forward.
    comments_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A short AI summary made on request: {"gist": str, "points": [str]}.
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary_language: Mapped[str | None] = mapped_column(String(8))
    summary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        UniqueConstraint("owner_id", "canonical_url", name="uq_link_snapshots_owner_page"),
        UniqueConstraint("id", "owner_id", name="uq_link_snapshots_id_owner"),
        CheckConstraint(check_in("status", LinkStatus), name="status"),
        Index("ix_link_snapshots_owner", "owner_id", "created_at"),
    )


class ThoughtLink(Base):
    """A thought links to a saved page. ``url`` is the link as written in that
    thought (the card hides exactly that text)."""

    __tablename__ = "thought_links"

    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        _owned_fk("thought_id", "thoughts"),
        ForeignKeyConstraint(
            ["link_id", "owner_id"],
            ["link_snapshots.id", "link_snapshots.owner_id"],
            ondelete="CASCADE",
            name="fk_link_id_owner_link_snapshots",
        ),
        Index("ix_thought_links_link", "link_id"),
    )
