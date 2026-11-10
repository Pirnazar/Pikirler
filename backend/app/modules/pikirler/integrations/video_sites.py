"""YouTube, TikTok and Instagram links, read the free and public way.

* YouTube: oEmbed (title, channel) and the public watch page (full
  description, exact publication time).
* TikTok: the public video page carries caption, author, time and cover.

Comments are not kept for these sites; the video itself is downloaded
separately (``media_download``).
* Instagram: only what the public post page shows to visitors without an
  account (author, date, caption). When Instagram demands a login, the link
  fails with ``link_login_required``; nothing is scraped behind a login.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app.core.config import get_settings
from app.modules.pikirler.integrations import web
from app.modules.pikirler.integrations.pages import Page, _date, _meta

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com"}
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class LoginRequired(web.FetchError):
    code = "link_login_required"


async def _json(url: str, *, max_bytes: int = 2 * 1024 * 1024) -> Any:
    got = await web.fetch(
        url, kinds=("application/json",), max_bytes=max_bytes, accept="application/json"
    )
    try:
        return json.loads(got.body)
    except ValueError as exc:
        raise web.Unreachable("unexpected answer") from exc


async def _html(url: str) -> web.Fetched:
    return await web.fetch(
        url,
        kinds=("text/html",),
        max_bytes=max(get_settings().link_max_page_bytes, 6 * 1024 * 1024),
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    )


# --- YouTube ------------------------------------------------------------------------


def youtube_id(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    candidate: str | None = None
    if host == "youtu.be" and segments:
        candidate = segments[0]
    elif host in YOUTUBE_HOSTS:
        if segments[:1] == ["watch"]:
            values = parse_qs(parts.query).get("v") or []
            candidate = values[0] if values else None
        elif len(segments) >= 2 and segments[0] in ("shorts", "live", "embed", "v"):
            candidate = segments[1]
    return candidate if candidate and _YT_ID.match(candidate) else None


def _player_response(html: str) -> dict[str, Any]:
    m = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;\s*(?:var |</script>)", html, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def from_youtube(video_id: str) -> Page:
    watch = f"https://www.youtube.com/watch?v={video_id}"
    got = await _html(watch + "&hl=en&persist_hl=1")
    html = got.body.decode("utf-8", errors="replace")
    player = _player_response(html)
    status = (player.get("playabilityStatus") or {}).get("status")
    details = player.get("videoDetails") or {}
    if status == "ERROR":  # "Video unavailable": removed, or never existed
        raise web.PageNotFound("video unavailable")
    micro = (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    channel_url = micro.get("ownerProfileUrl") or ""
    handle = channel_url.rstrip("/").rsplit("/", 1)[-1].lstrip("@") if "/@" in channel_url else None
    title = details.get("title") or _meta(html, "og:title")
    if not details:  # a consent or changed page: oEmbed still has the basics
        try:
            oembed = await _json(
                "https://www.youtube.com/oembed?" + urlencode({"url": watch, "format": "json"})
            )
        except web.Refused as exc:  # 400: no such video
            if exc.code == "link_http_400":
                raise web.PageNotFound("video unavailable") from exc
            raise
        if isinstance(oembed, dict):
            title = title or oembed.get("title")
            details = {"author": oembed.get("author_name")}
    thumbs = ((details.get("thumbnail") or {}).get("thumbnails")) or []
    image = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    if (
        thumbs
        and isinstance(thumbs[-1], dict)
        and str(thumbs[-1].get("url", "")).startswith("https://")
    ):
        image = thumbs[-1]["url"]
    seconds = details.get("lengthSeconds")
    page = Page(
        final_url=watch,
        kind="article",
        site_name="YouTube",
        title=title,
        author=details.get("author") or micro.get("ownerChannelName"),
        author_username=handle,
        published_at=_date(micro.get("publishDate") or _meta(html, "datePublished")),
        text=details.get("shortDescription") or _meta(html, "og:description", "description") or "",
        image_url=image,
    )
    if isinstance(seconds, str) and seconds.isdigit():
        page.duration_seconds = int(seconds)
    return page


# --- TikTok ---------------------------------------------------------------------------


def is_tiktok(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in TIKTOK_HOSTS


def _tiktok_time(video_id: str) -> datetime | None:
    if not video_id.isdigit():
        return None
    return datetime.fromtimestamp(int(video_id) >> 32, tz=UTC)


async def from_tiktok(url: str) -> Page:
    got = await _html(url)  # short vm.tiktok.com links redirect to the video
    html = got.body.decode("utf-8", errors="replace")
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        raise web.Unreachable("TikTok did not send the video data")
    try:
        scope = json.loads(m.group(1)).get("__DEFAULT_SCOPE__") or {}
    except ValueError as exc:
        raise web.Unreachable("TikTok did not send the video data") from exc
    detail = scope.get("webapp.video-detail") or {}
    if detail.get("statusCode") not in (0, None):
        raise web.PageNotFound(f"TikTok status {detail.get('statusCode')}")
    item = ((detail.get("itemInfo") or {}).get("itemStruct")) or {}
    if not item:
        raise web.PageNotFound("no video")
    author = item.get("author") or {}
    video = item.get("video") or {}
    video_id = str(item.get("id") or "")
    created = item.get("createTime")
    published = (
        datetime.fromtimestamp(int(created), tz=UTC)
        if isinstance(created, (int, str)) and str(created).isdigit()
        else _tiktok_time(video_id)
    )
    username = author.get("uniqueId")
    canonical = (
        f"https://www.tiktok.com/@{quote(str(username), safe='')}/video/{video_id}"
        if username and video_id
        else got.url
    )
    cover = video.get("cover") or video.get("originCover")
    page = Page(
        final_url=canonical,
        kind="post",
        site_name="TikTok",
        author=author.get("nickname") or username,
        author_username=username,
        published_at=published,
        text=item.get("desc") or "",
        image_url=cover if isinstance(cover, str) and cover.startswith("https://") else None,
    )
    duration = video.get("duration")
    if isinstance(duration, int):
        page.duration_seconds = duration
    return page


# --- Instagram --------------------------------------------------------------------------


def is_instagram_post(url: str) -> bool:
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    return (
        (parts.hostname or "").lower() in INSTAGRAM_HOSTS
        and len(segments) >= 2
        and segments[0] in ("p", "reel", "reels", "tv")
    )


_IG_DESCRIPTION = re.compile(
    r"^(?:[\d.,KMkm]+ likes?, [\d.,KMkm]+ comments? - )?"
    r"(?P<user>[\w.]+) on (?P<date>[A-Z][a-z]+ \d{1,2}, \d{4}): "
    r'"?(?P<caption>.*?)"?\.?$',
    re.S,
)


async def from_instagram(url: str) -> Page:
    got = await _html(url)
    html = got.body.decode("utf-8", errors="replace")
    if "/accounts/login" in got.url:
        raise LoginRequired("Instagram asks to log in")
    description = _meta(html, "og:description", "description")
    title = _meta(html, "og:title")
    if not description and not title:
        raise LoginRequired("Instagram shows this post only to logged-in users")
    username = caption = None
    published = None
    m = _IG_DESCRIPTION.match(unescape(description or "").strip())
    if m:
        username, caption = m.group("user"), m.group("caption")
        try:
            published = datetime.strptime(m.group("date"), "%B %d, %Y").replace(tzinfo=UTC)
        except ValueError:
            published = None
    name = None
    if title and " on Instagram" in title:
        name = title.split(" on Instagram", 1)[0].strip()
    image = _meta(html, "og:image")
    return Page(
        final_url=url.split("?", 1)[0],
        kind="post",
        site_name="Instagram",
        author=name or username,
        author_username=username,
        published_at=published,
        text=caption or description or "",
        image_url=image if image and image.startswith("https://") else None,
    )
