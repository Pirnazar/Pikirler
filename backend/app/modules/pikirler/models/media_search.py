from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

VECTOR_SQL = (
    "to_tsvector('simple'::regconfig, search_document) || "
    "to_tsvector('russian'::regconfig, search_document) || "
    "to_tsvector('english'::regconfig, search_document)"
)


class MediaEntry(Base):
    __tablename__ = "media_entries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE")
    )
    source_key: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    movie_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cinema_movies.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text)
    title_en: Mapped[str | None] = mapped_column(Text)
    creator: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    genres: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    poster: Mapped[str | None] = mapped_column(Text)
    signature: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("owner_id", "source_key"),
        UniqueConstraint("id", "owner_id"),
        Index("ix_media_entries_owner_kind_year", "owner_id", "kind", "year"),
        Index("ix_media_entries_genres", "genres", postgresql_using="gin"),
    )


class MediaPassage(Base):
    __tablename__ = "media_passages"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    entry_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    source_key: Mapped[str] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(16))
    part: Mapped[str] = mapped_column(String(16))
    body: Mapped[str] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    search_document: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed(VECTOR_SQL, persisted=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    __table_args__ = (
        ForeignKeyConstraint(
            ["entry_id", "owner_id"],
            ["media_entries.id", "media_entries.owner_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("entry_id", "source_key"),
        Index("ix_media_passages_owner_language", "owner_id", "language"),
        Index("ix_media_passages_search", "search_vector", postgresql_using="gin"),
        Index("ix_media_passages_entry", "entry_id"),
    )


class MediaIndexState(Base):
    __tablename__ = "media_index_states"
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
    )
    archive_stamp: Mapped[str | None] = mapped_column(String(100))
    phase: Mapped[str] = mapped_column(String(16), default="empty", server_default="empty")
    error: Mapped[str | None] = mapped_column(String(200))
    processed_items: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_items: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MediaSavedQuote(Base):
    __tablename__ = "media_saved_quotes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE")
    )
    thought_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("thoughts.id", ondelete="CASCADE")
    )
    quote_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("owner_id", "quote_key"),)
