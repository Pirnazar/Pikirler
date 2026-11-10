from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.modules.pikirler.models.enums import LinkStatus
from app.modules.pikirler.schemas.common import Schema


class LinkMedia(Schema):
    """The video downloaded from the link, served like any attachment."""

    id: uuid.UUID
    mime_type: str
    file_size: int
    processing_status: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    content_url: str
    thumbnail_url: str | None = None


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
    duration_seconds: int | None = Field(default=None, description="Videos: length")
    media_status: Literal["pending", "ready", "failed"] | None = Field(
        default=None, description="Downloaded video: null where none is downloaded"
    )
    media_error: str | None = None
    media: LinkMedia | None = Field(default=None, description="The downloaded video")
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
