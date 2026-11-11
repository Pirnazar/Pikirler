"""Reserved PostgreSQL schema for a future native YouTube importer.

The current Media section reads the separate Second Brain SQLite snapshot;
these additive tables are retained in the applied migration history.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Float,
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


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )


class YoutubeSource(Base):
    """The owner's YouTube account for reading the Liked playlist. The OAuth
    token (with its refresh token) is encrypted; it only allows reading."""

    __tablename__ = "youtube_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = _owner()
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    likes_playlist_id: Mapped[str | None] = mapped_column(String(100))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("owner_id", name="uq_youtube_sources_owner"),)


class YoutubeVideo(Base):
    __tablename__ = "youtube_videos"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = _owner()
    youtube_video_id: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    channel_id: Mapped[str | None] = mapped_column(String(64))
    channel_title: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    liked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when a full pass no longer finds the video in the Liked playlist.
    unliked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    category_id: Mapped[str | None] = mapped_column(String(16))
    topic_categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    content_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    kind_confidence: Mapped[str | None] = mapped_column(String(16))
    kind_reason: Mapped[str | None] = mapped_column(Text)
    artist: Mapped[str | None] = mapped_column(Text)
    track_title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "youtube_video_id", name="uq_youtube_videos_owner_video"),
        Index("ix_youtube_videos_liked", "owner_id", "liked_at"),
    )


class YoutubeTranscript(Base):
    """Captions of a video, from YouTube or transcribed with Whisper.

    ``status``: ok / failed / blocked. ``source``: youtube_manual,
    youtube_auto or whisper. ``segments``: [{start, duration, text}].
    """

    __tablename__ = "youtube_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = _owner()
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_videos.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
    language_code: Mapped[str | None] = mapped_column(String(32))
    language_name: Mapped[str | None] = mapped_column(String(100))
    is_generated: Mapped[bool | None] = mapped_column(Boolean)
    full_text: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    # Whisper was tried and failed: not queued for Whisper again unless asked.
    whisper_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("video_id", name="uq_youtube_transcripts_video"),)


class YoutubeLyrics(Base):
    """Song lyrics: ``status`` ok / instrumental / not_found / failed;
    ``lines``: [{start, text}] (start is null for unsynced lyrics)."""

    __tablename__ = "youtube_lyrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = _owner()
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("youtube_videos.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32))
    provider_track_id: Mapped[str | None] = mapped_column(String(64))
    matched_artist: Mapped[str | None] = mapped_column(Text)
    matched_track: Mapped[str | None] = mapped_column(Text)
    matched_duration_seconds: Mapped[float | None] = mapped_column(Float)
    is_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    language_code: Mapped[str | None] = mapped_column(String(32))
    full_text: Mapped[str | None] = mapped_column(Text)
    lines: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("video_id", name="uq_youtube_lyrics_video"),)
