"""Downloading the video of a YouTube / TikTok / Instagram link with yt-dlp.

Only those sites are allowed (``allowed_extractors``), no cookies or account
are ever used, size and duration are capped, and the file lands in a private
temporary directory that the caller removes. Up to 720p, merged to mp4 with
ffmpeg (already in the image for video thumbnails).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings

ALLOWED_EXTRACTORS = ["youtube", "TikTok", "Instagram"]
FORMAT = "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b[height<=720]/b"
MIME_BY_EXT = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mkv": "video/x-matroska",
    "mov": "video/quicktime",
}


class MediaError(Exception):
    code = "media_failed"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        if code:
            self.code = code


@dataclass(frozen=True)
class Downloaded:
    path: str
    mime_type: str
    filename: str


Downloader = Callable[[str, str], Downloaded]
_downloader: Downloader | None = None


def set_downloader(fn: Downloader | None) -> None:
    """Tests replace the network download."""
    global _downloader
    _downloader = fn


def _classify(message: str) -> str:
    text = message.lower()
    if "cookies" in text or "login" in text or "sign in" in text or "private" in text:
        return "media_login_required"
    if "larger than max-filesize" in text or "file is larger" in text:
        return "media_too_large"
    if "does not pass filter" in text or "skipping" in text:
        return "media_too_long"
    if "unavailable" in text or "removed" in text or "not available" in text or "404" in text:
        return "media_unavailable"
    return "media_failed"


def _yt_dlp(url: str, workdir: str) -> Downloaded:
    import yt_dlp  # heavy: only in the worker
    from yt_dlp.utils import DownloadError, match_filter_func

    s = get_settings()
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "cachedir": False,
        "outtmpl": os.path.join(workdir, "media.%(ext)s"),
        "format": FORMAT,
        "merge_output_format": "mp4",
        "max_filesize": s.link_media_max_mb * 1024 * 1024,
        # A list means "any of": short enough, or of unknown length.
        "match_filter": match_filter_func(
            [f"duration < {s.link_media_max_minutes * 60}", "!duration"]
        ),
        "socket_timeout": 30,
        "retries": 2,
        "allowed_extractors": ALLOWED_EXTRACTORS,
        "restrictfilenames": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise MediaError(str(exc)[:300], code=_classify(str(exc))) from exc
    if not isinstance(info, dict):
        raise MediaError("nothing downloaded")
    files = [f for f in os.listdir(workdir) if f.startswith("media.") and not f.endswith(".part")]
    if not files:
        # max_filesize and match_filter skip silently
        raise MediaError(
            "skipped", code="media_too_large" if info.get("filesize") else "media_too_long"
        )
    name = max(files, key=lambda f: os.path.getsize(os.path.join(workdir, f)))
    ext = name.rsplit(".", 1)[-1].lower()
    mime = MIME_BY_EXT.get(ext)
    if mime is None:
        raise MediaError(f"unexpected file type {ext}", code="media_failed")
    title = re.sub(r"[^\w\- ]+", "", str(info.get("title") or "video"))[:80].strip() or "video"
    return Downloaded(path=os.path.join(workdir, name), mime_type=mime, filename=f"{title}.{ext}")


def download(url: str, workdir: str) -> Downloaded:
    """Blocking: call through ``asyncio.to_thread``."""
    return (_downloader or _yt_dlp)(url, workdir)
