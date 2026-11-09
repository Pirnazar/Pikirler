from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.pikirler.models.enums import CrosspostStatus, CrosspostTarget
from app.modules.pikirler.schemas.common import Schema


class CrosspostOut(Schema):
    target: CrosspostTarget
    status: CrosspostStatus
    url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    sent_at: datetime | None = None
    updated_at: datetime


class TeswirlerConnect(Schema):
    identifier: str = Field(min_length=1, max_length=320, description="Username or e-mail")
    password: str = Field(min_length=1, max_length=1024, description="Used once, never stored")


class TeswirlerAccountOut(Schema):
    connected: bool
    username: str | None = None
    display_name: str | None = None
    profile_url: str | None = None
    connected_at: datetime | None = None
