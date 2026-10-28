from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from app.modules.pikirler.schemas.common import Schema


class SettingsOut(Schema):
    timezone: str
    ai_external_enabled: bool
    magic_word_enabled: bool
    providers: dict[str, Any]


class SettingsUpdate(Schema):
    timezone: str | None = Field(default=None, max_length=64)
    ai_external_enabled: bool | None = Field(
        default=None,
        description="Explicit consent to send selected thoughts to external "
        "AI providers (embeddings, LLM, ASR). Off by default.",
    )

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is not None:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("unknown timezone") from exc
        return value


class JobOut(Schema):
    id: uuid.UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    result: dict[str, Any] | None
    run_after: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
