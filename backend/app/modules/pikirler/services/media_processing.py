"""Derivatives of uploaded media, built in the worker.

* images: an EXIF-free preview (orientation applied, GPS/camera data dropped)
  and a thumbnail; the original stays untouched in storage.
* video: duration/dimensions via ffprobe, a poster thumbnail via ffmpeg.
* audio: duration via ffprobe.

Missing tools or unreadable formats are recorded in ``media_info`` instead of
failing the attachment: the original is still safely stored.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import tempfile
import uuid
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.pikirler.models import Attachment
from app.modules.pikirler.models.enums import AttachmentKind, ProcessingStatus
from app.modules.pikirler.services.media import storage_key
from app.storage import get_storage

PREVIEW_MAX = 2048
Image.MAX_IMAGE_PIXELS = 80_000_000  # decompression-bomb guard


def sanitize_image(data: bytes, max_side: int) -> tuple[bytes, str, int, int]:
    """Re-encode without any metadata. Returns (bytes, mime, width, height)."""
    with Image.open(io.BytesIO(data)) as raw:
        img: Image.Image = ImageOps.exif_transpose(raw)
        width, height = img.size
        if getattr(img, "is_animated", False):
            img.seek(0)
        img.thumbnail((max_side, max_side))
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        clean = Image.new("RGBA" if has_alpha else "RGB", img.size)
        clean.paste(img.convert("RGBA" if has_alpha else "RGB"))
        out = io.BytesIO()
        if has_alpha:
            clean.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png", width, height
        clean.save(out, format="JPEG", quality=85, optimize=True)
        return out.getvalue(), "image/jpeg", width, height


async def _ffprobe(path: str) -> dict[str, Any] | None:
    if shutil.which("ffprobe") is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        return None
    data: dict[str, Any] = json.loads(out or b"{}")
    return data


async def _video_poster(path: str, at_seconds: float) -> bytes | None:
    if shutil.which("ffmpeg") is None:
        return None
    fd, poster = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        # -map_metadata -1: the poster carries no container metadata (e.g. GPS).
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{at_seconds:.2f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-map_metadata",
            "-1",
            poster,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
        if proc.returncode != 0 or os.path.getsize(poster) == 0:
            return None
        with open(poster, "rb") as fh:  # noqa: ASYNC230
            return fh.read()
    finally:
        os.unlink(poster)


def _probe_summary(probe: dict[str, Any]) -> dict[str, Any]:
    fmt = probe.get("format", {})
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)
    return {
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        # Deliberately no tags: they can contain location and device data.
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
    }


async def _store_derivative(att: Attachment, variant: str, data: bytes, mime: str) -> str:
    key = storage_key(att.owner_id, att.id, variant)
    await get_storage().put_file(key, io.BytesIO(data), mime)
    return key


async def process(db: AsyncSession, attachment_id: uuid.UUID) -> dict[str, Any]:
    att = await db.get(Attachment, attachment_id)
    if att is None or att.deleted_at is not None:
        return {"skipped": "attachment gone"}
    if att.processing_status == ProcessingStatus.ready.value:
        return {"skipped": "already processed"}
    att.processing_status = ProcessingStatus.processing.value
    await db.commit()

    storage = get_storage()
    info: dict[str, Any] = dict(att.media_info or {})
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        await storage.download_to(att.storage_key, path)
        if att.kind == AttachmentKind.image.value:
            with open(path, "rb") as fh:  # noqa: ASYNC230
                data = fh.read()
            try:
                preview, mime, width, height = await asyncio.to_thread(
                    sanitize_image, data, PREVIEW_MAX
                )
                thumb, tmime, _, _ = await asyncio.to_thread(
                    sanitize_image, data, get_settings().thumbnail_size
                )
                ext = "png" if mime == "image/png" else "jpg"
                att.preview_key = await _store_derivative(att, f"preview.{ext}", preview, mime)
                att.thumbnail_key = await _store_derivative(att, f"thumb.{ext}", thumb, tmime)
                att.width, att.height = width, height
                info["preview_mime"] = mime
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                info["preview_error"] = type(exc).__name__
        elif att.kind in (AttachmentKind.video.value, AttachmentKind.audio.value):
            probe = await _ffprobe(path)
            if probe is None:
                info["probe_error"] = "ffprobe unavailable or failed"
            else:
                summary = _probe_summary(probe)
                info.update({k: v for k, v in summary.items() if v is not None})
                att.duration_seconds = summary["duration"]
                if att.kind == AttachmentKind.video.value:
                    att.width, att.height = summary["width"], summary["height"]
            if att.kind == AttachmentKind.video.value:
                at = min(1.0, (att.duration_seconds or 0) / 2)
                poster = await _video_poster(path, at)
                if poster is None:
                    info["thumbnail_error"] = "ffmpeg unavailable or failed"
                else:
                    thumb, tmime, _, _ = await asyncio.to_thread(
                        sanitize_image, poster, get_settings().thumbnail_size
                    )
                    att.thumbnail_key = await _store_derivative(att, "thumb.jpg", thumb, tmime)
        att.media_info = info
        att.processing_status = ProcessingStatus.ready.value
        await db.commit()
        return {"status": "ready", **{k: v for k, v in info.items() if k.endswith("error")}}
    finally:
        os.unlink(path)
