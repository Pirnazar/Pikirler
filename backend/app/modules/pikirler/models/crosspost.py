"""Copies of thoughts published elsewhere, and the accounts used for that.

A thought is only ever sent out when the owner ticks a target for that thought.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.pikirler.models.enums import CrosspostStatus, CrosspostTarget, check_in
from app.modules.pikirler.models.thought import _owned_fk


class ExternalAccount(Base):
    """The owner's account on another site. Session tokens are encrypted
    (``app.core.secretbox``); the password is never stored."""

    __tablename__ = "external_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_user_id: Mapped[str | None] = mapped_column(String(200))
    username: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(200))
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "provider", name="uq_external_accounts_owner_provider"),
        CheckConstraint(check_in("provider", CrosspostTarget), name="provider"),
    )


class Crosspost(Base):
    """One thought sent to one target. ``sending`` is committed before the
    request leaves, so a crash mid-request ends as ``unknown``, never a resend."""

    __tablename__ = "crossposts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
    )
    thought_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    remote_id: Mapped[str | None] = mapped_column(String(200))
    remote_url: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("thought_id", "target", name="uq_crossposts_thought_target"),
        _owned_fk("thought_id", "thoughts"),
        CheckConstraint(check_in("target", CrosspostTarget), name="target"),
        CheckConstraint(check_in("status", CrosspostStatus), name="status"),
        Index("ix_crossposts_owner", "owner_id", "created_at"),
    )
