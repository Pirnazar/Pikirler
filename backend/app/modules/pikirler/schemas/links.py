from __future__ import annotations

import uuid
from datetime import datetime

from app.modules.pikirler.models.enums import LinkStatus
from app.modules.pikirler.schemas.common import Schema


class LinkOut(Schema):
    """A link of a thought and the copy of its page kept in Pikirler."""

    id: uuid.UUID
    url: str
    final_url: str | None = None
    status: LinkStatus
    error_code: str | None = None
    site_name: str | None = None
    title: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    excerpt: str | None = None
    has_text: bool = False
    reading_minutes: int | None = None
    cover_url: str | None = None
    fetched_at: datetime | None = None


class LinkDetail(LinkOut):
    text: str = ""
