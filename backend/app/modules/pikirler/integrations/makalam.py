"""makalam.com (a Turkmen social network: short posts and long articles).

Its pages carry schema.org JSON-LD for search engines, which is all a copy
needs and is more stable than the page layout:

* a post (``/@user/post/123``) is a ``SocialMediaPosting``: text, author,
  @handle, exact time, pictures (``image``), videos (``video``) and the
  comments with their replies (``comment``);
* an article (``/<slug>/``) is an ``Article``: headline, author, dates; its
  text and pictures come from the page's article body (``ck-content``).

Pictures makalam generates itself (``/api/og/...`` share cards) are skipped.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from app.core.config import get_settings
from app.modules.pikirler.integrations import web
from app.modules.pikirler.integrations.pages import MAX_MEDIA, Page, _date, html_to_text

HOSTS = {"makalam.com", "www.makalam.com"}
SITE = "makalam"
_LD = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.S | re.I)
_IMG = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)
_DIV = re.compile(r"<(/?)div\b", re.I)


def is_makalam(url: str) -> bool:
    return (urlsplit(url).hostname or "").lower() in HOSTS


def _ld(html: str) -> dict[str, Any] | None:
    for block in _LD.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") in (
                "SocialMediaPosting",
                "Article",
                "BlogPosting",
                "NewsArticle",
            ):
                return item
    return None


def _person(value: Any) -> tuple[str | None, str | None]:
    person = value if isinstance(value, dict) else {}
    name = person.get("name") if isinstance(person.get("name"), str) else None
    handle = person.get("alternateName")
    handle = handle.lstrip("@") if isinstance(handle, str) and handle.strip("@") else None
    return (name.strip() if name else None) or handle, handle


def _own(url: Any, base: str) -> str | None:
    """An absolute https address of a picture or video the author added."""
    if not isinstance(url, str):
        return None
    full = urljoin(base, url)
    if not full.startswith("https://") or "/api/og/" in full or "/avatar-" in full:
        return None
    return full


def _media(ld: dict[str, Any], base: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def add(url: str | None, kind: str) -> None:
        if url and all(m["url"] != url for m in found):
            found.append({"url": url, "kind": kind})

    images = ld.get("image")
    for image in images if isinstance(images, list) else [images]:
        if isinstance(image, dict):
            image = image.get("contentUrl") or image.get("url")
        add(_own(image, base), "image")
    videos = ld.get("video")
    for video in videos if isinstance(videos, list) else [videos]:
        if isinstance(video, dict):
            video = video.get("contentUrl") or video.get("url")
        add(_own(video, base), "video")
    return found[:MAX_MEDIA]


def _likes(item: dict[str, Any]) -> int:
    for stat in item.get("interactionStatistic") or []:
        if isinstance(stat, dict) and str(stat.get("interactionType", "")).endswith("LikeAction"):
            count = stat.get("userInteractionCount")
            return count if isinstance(count, int) else 0
    return 0


def _comments(items: Any, base: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name, handle = _person(item.get("author"))
        created = _date(item.get("datePublished"))
        url = item.get("url") if isinstance(item.get("url"), str) else None
        pictures = _media(item, base)
        out.append(
            {
                "id": url,
                "author": name,
                "username": handle,
                "text": str(item.get("text") or "").strip(),
                "quote": None,
                "created_at": created.isoformat() if created else None,
                "likes": _likes(item),
                "image_url": pictures[0]["url"] if pictures else None,
                "replies": _comments(item.get("comment"), base),
            }
        )
    out.sort(key=lambda c: c["created_at"] or "")
    return out


def _count(comments: list[dict[str, Any]]) -> int:
    return sum(1 + _count(c.get("replies") or []) for c in comments)


def _article_body(html: str) -> str:
    """The inner HTML of the article body (``div.ck-content``)."""
    start = re.search(r"<div\b[^>]*class=[\"'][^\"']*\bck-content\b[^\"']*[\"'][^>]*>", html, re.I)
    if not start:
        return ""
    depth, pos = 1, start.end()
    for tag in _DIV.finditer(html, pos):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return html[pos : tag.start()]
    return html[pos:]


async def from_makalam(url: str) -> Page:
    got = await web.fetch(
        url,
        kinds=("text/html",),
        max_bytes=get_settings().link_max_page_bytes,
        accept="text/html,application/xhtml+xml;q=0.9",
    )
    html = got.body.decode("utf-8", errors="replace")
    ld = _ld(html)
    if ld is None:
        # Not a post or an article (or makalam changed its markup): do not
        # save the page chrome as if it were the text.
        raise web.Unreachable("makalam page without post data", code="link_unsupported")
    declared_url = ld.get("url")
    canonical: str = declared_url if isinstance(declared_url, str) else got.url
    name, handle = _person(ld.get("author"))
    published = _date(ld.get("datePublished"))
    modified = _date(ld.get("dateModified"))
    edited = (
        modified if modified and published and (modified - published).total_seconds() > 60 else None
    )

    if ld.get("@type") == "SocialMediaPosting":
        media = _media(ld, canonical)
        comments = _comments(ld.get("comment"), canonical)
        declared = ld.get("commentCount")
        return Page(
            final_url=canonical,
            kind="post",
            site_name=SITE,
            title=None,
            author=name,
            author_username=handle,
            published_at=published,
            edited_at=edited,
            text=str(ld.get("text") or ""),
            image_url=next((m["url"] for m in media if m["kind"] == "image"), None),
            media=media,
            comments=comments,
            # Only a full list may mark missing comments as deleted.
            comments_complete=isinstance(declared, int) and _count(comments) >= declared,
        )

    body = _article_body(html)
    pictures: list[dict[str, str]] = []
    for src in _IMG.findall(body):
        own = _own(src, canonical)
        if own and all(p["url"] != own for p in pictures):
            pictures.append({"url": own, "kind": "image"})
    return Page(
        final_url=canonical,
        kind="article",
        site_name=SITE,
        title=ld.get("headline") if isinstance(ld.get("headline"), str) else None,
        author=name,
        author_username=handle,
        published_at=published,
        edited_at=edited,
        text=html_to_text(body) if body else str(ld.get("description") or ""),
        excerpt=ld.get("description") if isinstance(ld.get("description"), str) else None,
        image_url=pictures[0]["url"] if pictures else None,
        media=pictures[:MAX_MEDIA],
    )
