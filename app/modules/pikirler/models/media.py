from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.pikirler.models.enums import (
    AttachmentKind,
    ProcessingStatus,
    TranscriptionStatus,
    check_in,
)
from app.modules.pikirler.models.thought import Thought, _owned_fk


class Attachment(Base):
    """Metadata of a privately stored object. The bytes live in object storage."""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    # Derived from ids only, never from the client filename.
    storage_key: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    declared_mime_type: Mapped[str] = mapped_column(String(127), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    processing_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending_upload"
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    # EXIF-free derivatives; the original is kept untouched but not served by default.
    preview_key: Mapped[str | None] = mapped_column(String(300))
    thumbnail_key: Mapped[str | None] = mapped_column(String(300))
    media_info: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_attachments_id_owner"),
        CheckConstraint(check_in("kind", AttachmentKind), name="kind"),
        CheckConstraint(check_in("processing_status", ProcessingStatus), name="processing_status"),
        CheckConstraint("file_size >= 0", name="file_size_positive"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_sha256"),
        Index("ix_attachments_owner_created", "owner_id", text("created_at DESC")),
        Index(
            "ix_attachments_pending",
            "processing_status",
            "created_at",
            postgresql_where=text("processing_status = 'pending_upload'"),
        ),
    )


class ThoughtAttachment(Base):
    __tablename__ = "thought_attachments"

    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thought: Mapped[Thought] = relationship(
        back_populates="attachment_links",
        lazy="raise",
        primaryjoin="Thought.id == foreign(ThoughtAttachment.thought_id)",
    )
    attachment: Mapped[Attachment] = relationship(
        lazy="raise", primaryjoin="Attachment.id == foreign(ThoughtAttachment.attachment_id)"
    )

    __table_args__ = (
        _owned_fk("thought_id", "thoughts"),
        _owned_fk("attachment_id", "attachments"),
        Index("ix_thought_attachments_attachment", "attachment_id"),
    )


class VoiceTranscription(Base):
    __tablename__ = "voice_transcriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    attachment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thought_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("thoughts.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    requested_language: Mapped[str | None] = mapped_column(String(8))
    detected_language: Mapped[str | None] = mapped_column(String(8))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    # Never overwritten after completion: what the ASR model actually produced.
    raw_text: Mapped[str | None] = mapped_column(Text)
    # The owner's corrections; NULL until edited.
    edited_text: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        _owned_fk("attachment_id", "attachments"),
        CheckConstraint(check_in("status", TranscriptionStatus), name="status"),
        CheckConstraint(
            "requested_language IS NULL OR requested_language IN ('tk', 'ru', 'en', 'zh')",
            name="requested_language",
        ),
        Index("ix_voice_transcriptions_owner", "owner_id", text("created_at DESC")),
        Index("ix_voice_transcriptions_attachment", "attachment_id"),
        Index("ix_voice_transcriptions_thought", "thought_id"),
    )
