"""Clean copies of a thought's pictures and videos for another site.

The stored original keeps whatever the camera wrote into it (GPS position,
device, time). Nothing leaves Pikirler like that:

* a picture is re-encoded by Pillow without any metadata (orientation applied,
  at most 2560 px on the long side). A GIF is passed as it is: it carries no
  camera data, and re-encoding would stop its animation;
* a video is remuxed by ffmpeg with ``-map_metadata -1`` (no re-encoding, so
  quality and size stay). Without ffmpeg a video is not sent at all.

Each site says which files it takes (``FileRules``); a file it would refuse
(type, size) is left out, and so is everything past its count limit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pikirler.models import Attachment, ThoughtAttachment
from app.modules.pikirler.models.enums import AttachmentKind
from app.modules.pikirler.services.media_processing import sanitize_image
from app.storage import get_storage

log = logging.getLogger("pikirler.crosspost")

IMAGE_MAX_SIDE = 2560
MB = 1024 * 1024
_VIDEO_EXT = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm"}


@dataclass(frozen=True)
class OutgoingFile:
    kind: str  # image | video
    name: str
    mime: str
    data: bytes


@dataclass(frozen=True)
class FileRules:
    """What a site takes in one post."""

    max_count: int
    max_bytes: dict[str, int]  # kind -> limit; a kind not listed is not taken
    image_types: frozenset[str] | None = None  # None: any picture
    video_types: frozenset[str] | None = None  # None: any video

    def takes(self, kind: str, mime: str) -> bool:
        if kind not in self.max_bytes:
            return False
        allowed = self.image_types if kind == "image" else self.video_types
        return allowed is None or mime in allowed


async def _attachments(db: AsyncSession, thought_id: uuid.UUID) -> list[Attachment]:
    rows = await db.execute(
        select(Attachment)
        .join(ThoughtAttachment, ThoughtAttachment.attachment_id == Attachment.id)
        .where(
            ThoughtAttachment.thought_id == thought_id,
            Attachment.deleted_at.is_(None),
            Attachment.uploaded_at.is_not(None),
            Attachment.kind.in_([AttachmentKind.image.value, AttachmentKind.video.value]),
        )
        .order_by(ThoughtAttachment.position, ThoughtAttachment.created_at)
    )
    return list(rows.scalars())


def _stem(att: Attachment) -> str:
    stem = Path(att.safe_filename or "file").stem[:80] or "file"
    return stem


async def _strip_video(source: str, ext: str) -> bytes | None:
    if shutil.which("ffmpeg") is None:
        return None
    fd, target = tempfile.mkstemp(suffix=f".{ext}")
    os.close(fd)
    try:
        args = ["ffmpeg", "-y", "-v", "error", "-i", source, "-map", "0", "-map_metadata", "-1"]
        args += ["-map_chapters", "-1", "-c", "copy"]
        if ext in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
        proc = await asyncio.create_subprocess_exec(
            *args, target, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), timeout=300)
        if proc.returncode != 0 or os.path.getsize(target) == 0:
            return None
        return await asyncio.to_thread(Path(target).read_bytes)
    finally:
        os.unlink(target)


async def _clean_copy(att: Attachment) -> OutgoingFile | None:
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        await get_storage().download_to(att.storage_key, path)
        if att.kind == AttachmentKind.image.value:
            data = await asyncio.to_thread(Path(path).read_bytes)
            if att.mime_type == "image/gif":
                return OutgoingFile("image", f"{_stem(att)}.gif", "image/gif", data)
            try:
                clean, mime, _, _ = await asyncio.to_thread(sanitize_image, data, IMAGE_MAX_SIDE)
            except (UnidentifiedImageError, OSError, ValueError):
                return None  # a format Pillow cannot read is not sent with its metadata
            ext = "png" if mime == "image/png" else "jpg"
            return OutgoingFile("image", f"{_stem(att)}.{ext}", mime, clean)
        ext = _VIDEO_EXT.get(att.mime_type, "mp4")
        video = await _strip_video(path, ext)
        if video is None:
            return None
        return OutgoingFile("video", f"{_stem(att)}.{ext}", att.mime_type, video)
    finally:
        os.unlink(path)


async def collect(db: AsyncSession, thought_id: uuid.UUID, rules: FileRules) -> list[OutgoingFile]:
    """Clean copies of the files the site takes, in the thought's order."""
    picked: list[OutgoingFile] = []
    for att in await _attachments(db, thought_id):
        if len(picked) >= rules.max_count:
            break
        if not rules.takes(att.kind, att.mime_type):
            continue
        try:
            copy = await _clean_copy(att)
        except Exception:  # storage or ffmpeg trouble: send the rest
            log.warning("crosspost_file_skipped", extra={"attachment_id": str(att.id)})
            continue
        if copy is None or len(copy.data) > rules.max_bytes[copy.kind]:
            continue
        if not rules.takes(copy.kind, copy.mime):
            continue
        picked.append(copy)
    return picked
