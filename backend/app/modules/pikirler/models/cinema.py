from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
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


class CinemaMovie(Base):
    __tablename__ = "cinema_movies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE")
    )
    imdb_id: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    year: Mapped[str | None] = mapped_column(String(16))
    poster: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    runtime: Mapped[str | None] = mapped_column(String(80))
    rating: Mapped[str | None] = mapped_column(String(16))
    genres: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    cast: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    director: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    aliases: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    localized: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    metadata_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    subtitle_languages: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    subtitle_status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    subtitle_error: Mapped[str | None] = mapped_column(String(500))
    watch_status: Mapped[str] = mapped_column(
        String(16), default="unlisted", server_default="unlisted"
    )
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    user_rating: Mapped[int | None] = mapped_column(Integer)
    user_comment: Mapped[str] = mapped_column(Text, default="", server_default="")
    watched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("watch_status IN ('unlisted', 'want', 'watched')", name="watch_status"),
        CheckConstraint("user_rating IS NULL OR user_rating BETWEEN 1 AND 10", name="user_rating"),
        UniqueConstraint("owner_id", "imdb_id"),
        Index("ix_cinema_movies_owner", "owner_id"),
    )


class CinemaSubtitle(Base):
    __tablename__ = "cinema_subtitles"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movie_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cinema_movies.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))
    provider_id: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    cue_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("movie_id", "source", "provider_id"),)


class CinemaCue(Base):
    __tablename__ = "cinema_cues"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    subtitle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cinema_subtitles.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple'::regconfig, body) || "
            "to_tsvector('russian'::regconfig, body) || "
            "to_tsvector('english'::regconfig, body)",
            persisted=True,
        ),
    )
    __table_args__ = (
        UniqueConstraint("subtitle_id", "position"),
        CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="cue_times"),
        Index("ix_cinema_cues_search", "search_vector", postgresql_using="gin"),
    )
