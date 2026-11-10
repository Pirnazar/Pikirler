from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.pikirler.models.enums import LinkStatus
from app.modules.pikirler.schemas.common import Schema


class LinkOut(Schema):
    """A link of a thought and the copy of its page kept in Pikirler."""

    id: uuid.UUID
    url: str
    final_url: str | None = None
    status: LinkStatus
    error_code: str | None = None
    kind: Literal["article", "post"] = Field(
        default="article", description="post: a short post (Teswirler pikir), shown as a quote"
    )
    site_name: str | None = None
    title: str | None = None
    author: str | None = None
    author_username: str | None = None
    author_url: str | None = None
    published_at: datetime | None = None
    edited_at: datetime | None = None
    comment_count: int | None = Field(
        default=None, description="Comments incl. replies still on the site; null when none kept"
    )
    deleted_comment_count: int = Field(default=0, description="Kept comments deleted on the site")
    synced_at: datetime | None = Field(default=None, description="Last check against the original")
    source_deleted_at: datetime | None = Field(
        default=None, description="When the original was found deleted; the copy is kept"
    )
    post_text: str | None = Field(default=None, description="Full text of a short post")
    excerpt: str | None = None
    has_text: bool = False
    reading_minutes: int | None = None
    cover_url: str | None = None
    fetched_at: datetime | None = None


class SavedComment(Schema):
    id: str | None = None
    author: str | None = None
    username: str | None = None
    author_url: str | None = None
    text: str
    quote: str | None = None
    created_at: datetime | None = None
    likes: int = 0
    image_url: str | None = None
    deleted_at: datetime | None = Field(
        default=None, description="Set when the comment disappeared from the site"
    )
    replies: list[SavedComment] = Field(default_factory=list)


class LinkDetail(LinkOut):
    text: str = ""
    comments: list[SavedComment] = Field(default_factory=list)
