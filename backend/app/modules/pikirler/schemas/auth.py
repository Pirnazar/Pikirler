from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.modules.pikirler.schemas.common import Schema


class LoginRequest(Schema):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class OwnerOut(Schema):
    id: uuid.UUID
    email: str
    display_name: str | None
    timezone: str
    ai_external_enabled: bool


class SessionState(Schema):
    owner: OwnerOut
    session_id: uuid.UUID | None
    magic_word_enabled: bool
    unlocked: bool
    csrf_token: str | None = Field(
        default=None,
        description="Only returned at login. Also set as the readable CSRF cookie; "
        "send it back in the X-CSRF-Token header on every unsafe request.",
    )


class UnlockRequest(Schema):
    magic_word: str = Field(min_length=1, max_length=256)


class UnlockResponse(Schema):
    unlocked_until: datetime


class MagicWordUpdate(Schema):
    password: str = Field(min_length=1, max_length=1024)
    magic_word: str | None = Field(
        default=None, min_length=4, max_length=256, description="null removes the Magic Word"
    )


class PasswordChange(Schema):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, value: str) -> str:
        if value.strip() != value or len(set(value)) < 6:
            raise ValueError("password is too weak")
        return value


class SessionOut(Schema):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool = False
