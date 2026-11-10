"""Turning a fetched link into a readable page: title, author, date, text.

Teswirler links are read through Teswirler's public API (exact title, author
and text). Every other site goes through trafilatura, a readability-style
extractor that keeps the article and drops menus, ads and comments.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from app.core.config import get_settings
from app.modules.pikirler.integrations import web

MAX_TEXT_CHARS = 200_000
EXCERPT_CHARS = 280

TESWIRLER_HOSTS = {"teswirler.com", "www.teswirler.com"}
# First path segments on teswirler.com that are app pages, not article slugs.
TESWIRLER_RESERVED = {
    "pikirler", "sahsy-hat", "bildirisler", "tolegler", "gozleg", "ayarlar", "sazlamalar",
    "yaz", "yazmak", "login", "register", "giris", "biz-barada", "habarlas", "gizlinlik",
    "duzgunler", "temalar", "topics", "search", "settings", "auth",
}  # fmt: skip


@dataclass
class Page:
    final_url: str
    # "article" (has a title, shown as a card) or "post" (a short post such as
    # a Teswirler pikir, shown as a quote).
    kind: str = "article"
    site_name: str | None = None
    title: str | None = None
    author: str | None = None
    author_username: str | None = None
    published_at: datetime | None = None
    edited_at: datetime | None = None
    text: str = ""
    excerpt: str | None = None
    image_url: str | None = None
    # Teswirler only: every comment with its replies (None = not available).
    comments: list[dict[str, Any]] | None = None
    # True when every comment page and reply list was read: only then may a
    # comment missing from ``comments`` be taken as deleted on the site.
    comments_complete: bool = False
    duration_seconds: int | None = None

    def finish(self) -> Page:
        self.text = tidy(self.text)[:MAX_TEXT_CHARS]
        self.title = _clean(self.title, 1000)
        self.author = _clean(self.author, 300)
        self.author_username = _clean(self.author_username, 200)
        self.site_name = _clean(self.site_name, 200)
        excerpt = _clean(self.excerpt, 2000) or (self.text[:EXCERPT_CHARS] if self.text else None)
        if excerpt and len(excerpt) > EXCERPT_CHARS:
            excerpt = excerpt[: EXCERPT_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
        self.excerpt = excerpt
        if not self.title and self.text:
            first = self.text.split("\n", 1)[0]
            self.title = first if len(first) <= 120 else first[:119].rsplit(" ", 1)[0] + "…"
        return self


def _clean(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", unescape(value)).strip()
    return value[:limit] or None


def tidy(text: str) -> str:
    """Paragraphs separated by one blank line, no runs of spaces."""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in text.replace("\r", "").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --- HTML fragment -> text ---------------------------------------------------------

_BLOCKS = {
    "p", "div", "section", "article", "blockquote", "pre", "ul", "ol", "table", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6", "figure", "figcaption", "hr",
}  # fmt: skip


class _TextOf(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self.skip += 1
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in _BLOCKS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "template"):
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(fragment: str) -> str:
    parser = _TextOf()
    parser.feed(fragment)
    parser.close()
    return tidy("".join(parser.parts))


# --- Teswirler ------------------------------------------------------------------


def teswirler_api_path(url: str) -> str | None:
    """The API path for a Teswirler article or pikir link, else None."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in TESWIRLER_HOSTS:
        return None
    segments = [unquote(s) for s in parts.path.split("/") if s]
    if (
        len(segments) == 1
        and segments[0] not in TESWIRLER_RESERVED
        and not segments[0].startswith("@")
    ):
        return "/blogs/" + quote(segments[0], safe="")
    if len(segments) == 2 and segments[0] == "pikirler":
        return "/pikirler/" + quote(segments[1], safe="")
    return None


def _person(value: Any) -> tuple[str | None, str | None]:
    """(display name, username) of a Teswirler author object."""
    person: dict[str, Any] = value if isinstance(value, dict) else {}
    username = person.get("username") if isinstance(person.get("username"), str) else None
    name = person.get("name") if isinstance(person.get("name"), str) else None
    return name or username, username


def _teswirler_page(url: str, api_path: str, data: dict[str, Any]) -> Page:
    name, username = _person(data.get("author"))
    images = data.get("images")
    if api_path.startswith("/blogs/"):
        raw_body = data.get("body")
        body = raw_body if isinstance(raw_body, str) else ""
        published = _date(data.get("publishedAt") or data.get("createdAt"))
        updated = _date(data.get("updatedAt"))
        edited = (
            updated
            if updated and published and (updated - published).total_seconds() > 60
            else None
        )
        return Page(
            final_url=url,
            site_name="Teswirler",
            title=data.get("title"),
            author=name,
            author_username=username,
            published_at=published,
            edited_at=edited,
            text=html_to_text(body),
            excerpt=data.get("excerpt"),
            image_url=data.get("coverImage") if isinstance(data.get("coverImage"), str) else None,
        )
    raw_content = data.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    first_image = images[0] if isinstance(images, list) and images else None
    if isinstance(first_image, dict):
        first_image = first_image.get("url")
    created = _date(data.get("createdAt"))
    edited = _date(data.get("updatedAt"))
    return Page(
        final_url=url,
        kind="post",
        site_name="Teswirler",
        title=None,  # a pikir has no title: the first line is used
        author=name,
        author_username=username,
        published_at=created,
        edited_at=edited
        if edited and created and (edited - created).total_seconds() > 60
        else None,
        text=content,
        image_url=first_image if isinstance(first_image, str) else None,
    )


async def _from_teswirler(url: str, api_path: str) -> Page | None:
    api = get_settings().teswirler_api_url.rstrip("/")
    got = await web.fetch(
        api + api_path,
        kinds=("application/json",),
        max_bytes=get_settings().link_max_page_bytes,
        accept="application/json",
    )
    try:
        data = json.loads(got.body).get("data")
    except (ValueError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    page = _teswirler_page(url, api_path, data)
    item_id = data.get("id")
    if isinstance(item_id, str) and item_id:
        kind = "blogs" if api_path.startswith("/blogs/") else "pikirler"
        try:
            page.comments, page.comments_complete = await _teswirler_comments(
                f"{api}/{kind}/{quote(item_id, safe='')}"
            )
        except web.FetchError:
            page.comments = None  # the article is still saved, just without comments
    return page


COMMENT_PAGE = 50  # Teswirler refuses larger pages
MAX_COMMENTS = 2000
MAX_COMMENT_REQUESTS = 300
MAX_REPLY_DEPTH = 20


async def _teswirler_comments(base: str) -> tuple[list[dict[str, Any]], bool]:
    """Every comment of an article or pikir, oldest first, with nested replies,
    and whether the list is complete (no limit was hit, nothing was skipped)."""
    budget = {"requests": MAX_COMMENT_REQUESTS, "comments": MAX_COMMENTS}
    state = {"complete": True}

    async def page_of(path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        skip = 0
        while True:
            if budget["requests"] <= 0 or budget["comments"] <= 0:
                state["complete"] = False
                break
            budget["requests"] -= 1
            got = await web.fetch(
                f"{path}?skip={skip}&take={COMMENT_PAGE}",
                kinds=("application/json",),
                max_bytes=get_settings().link_max_page_bytes,
                accept="application/json",
            )
            try:
                data = json.loads(got.body).get("data")
            except (ValueError, AttributeError):
                state["complete"] = False
                break
            items = data if isinstance(data, list) else (data or {}).get("items")
            if not isinstance(items, list):
                state["complete"] = False
                break
            rows.extend(i for i in items if isinstance(i, dict))
            budget["comments"] -= len(items)
            more = data.get("hasMore") if isinstance(data, dict) else len(items) >= COMMENT_PAGE
            if not more or not items:
                break
            skip += len(items)
        return rows

    async def tree(path: str, depth: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for raw in await page_of(path):
            name, username = _person(raw.get("author"))
            comment_id = raw.get("id")
            quote_text = raw.get("quote")
            replies: list[dict[str, Any]] = []
            has_replies = isinstance(raw.get("repliesCount"), int) and raw["repliesCount"] > 0
            if has_replies and depth >= MAX_REPLY_DEPTH:
                state["complete"] = False
            elif has_replies and isinstance(comment_id, str):
                replies = await tree(
                    f"{base}/comments/{quote(comment_id, safe='')}/replies", depth + 1
                )
            created = _date(raw.get("createdAt"))
            image = raw.get("image")
            if isinstance(image, dict):
                image = image.get("url")
            out.append(
                {
                    "id": str(comment_id) if comment_id else None,
                    "author": _clean(name, 300),
                    "username": _clean(username, 200),
                    "text": tidy(str(raw.get("content") or ""))[:20_000],
                    "quote": tidy(quote_text)[:2000] if isinstance(quote_text, str) else None,
                    "created_at": created.isoformat() if created else None,
                    "likes": raw.get("likesCount") if isinstance(raw.get("likesCount"), int) else 0,
                    "image_url": image
                    if isinstance(image, str) and image.startswith("https://")
                    else None,
                    "replies": replies,
                }
            )
        out.sort(key=lambda c: c["created_at"] or "")
        return out

    comments = await tree(f"{base}/comments", 0)
    return comments, state["complete"]


# --- X (Twitter) ------------------------------------------------------------------
#
# X's own pages need JavaScript and its API is paid, but X serves every public
# post through oEmbed for free and without keys: text, author name, @username
# and date. A deleted or private post answers 404, which the background check
# turns into "original deleted". Replies are not available this way.

X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com",
           "mobile.x.com"}  # fmt: skip
X_OEMBED = "https://publish.x.com/oembed"
_X_EPOCH_MS = 1288834974657  # snowflake ids carry their creation time


def x_status(url: str) -> tuple[str, str] | None:
    """(username, post id) of a link to an X post, else None."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in X_HOSTS:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) >= 3 and segments[1] in ("status", "statuses") and segments[2].isdigit():
        return segments[0], segments[2]
    return None


def _snowflake_time(post_id: str) -> datetime | None:
    value = int(post_id)
    if value < 2**40:  # posts from before 2010 have plain counters
        return None
    return datetime.fromtimestamp(((value >> 22) + _X_EPOCH_MS) / 1000, tz=UTC)


class _XPost(HTMLParser):
    """Reads the <blockquote> oEmbed returns: <p>text</p>&mdash; Name (@user) <a>date</a>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.after: list[str] = []
        self.in_p = 0
        self.done_p = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p" and not self.done_p:
            self.in_p += 1
        elif tag == "br" and self.in_p:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self.in_p:
            self.in_p -= 1
            self.done_p = self.in_p == 0

    def handle_data(self, data: str) -> None:
        (self.text if self.in_p else self.after).append(data)


async def _from_x(url: str, username: str, post_id: str) -> Page:
    canonical = f"https://x.com/{quote(username, safe='')}/status/{post_id}"
    got = await web.fetch(
        f"{X_OEMBED}?url={quote(canonical, safe='')}&omit_script=1&dnt=1&lang=en",
        kinds=("application/json",),
        max_bytes=512 * 1024,
        accept="application/json",
    )
    try:
        data = json.loads(got.body)
    except ValueError as exc:
        raise web.Unreachable("unexpected answer from X") from exc
    if not isinstance(data, dict) or not isinstance(data.get("html"), str):
        raise web.Unreachable("unexpected answer from X")
    parser = _XPost()
    parser.feed(data["html"])
    parser.close()
    handle = urlsplit(str(data.get("author_url") or "")).path.strip("/") or username
    published = _snowflake_time(post_id)
    if published is None:
        tail = "".join(parser.after)
        m = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*$", tail.strip())
        if m:
            try:
                published = datetime.strptime(m.group(1), "%B %d, %Y").replace(tzinfo=UTC)
            except ValueError:
                published = None
    return Page(
        final_url=canonical,
        kind="post",
        site_name="X",
        author=data.get("author_name") if isinstance(data.get("author_name"), str) else handle,
        author_username=handle,
        published_at=published,
        text="".join(parser.text),
    )


# --- any other site ---------------------------------------------------------------


def _meta(html: str, *names: str) -> str | None:
    for name in names:
        for pattern in (
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]*content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{re.escape(name)}["\']',
        ):
            m = re.search(pattern, html, re.I)
            if m:
                return unescape(m.group(1))
    return None


def _extract_html(body: bytes, url: str) -> Page:
    import trafilatura  # heavy import: only in the worker, only when needed

    result = trafilatura.bare_extraction(
        body,
        url=url,
        with_metadata=True,
        include_comments=False,
        include_tables=False,
        include_images=False,
        favor_precision=True,
    )
    doc: dict[str, Any] = {}
    if isinstance(result, dict):
        doc = result
    elif result is not None and hasattr(result, "as_dict"):
        doc = result.as_dict()
    head = body[:300_000].decode("utf-8", errors="replace")
    title = doc.get("title") or _meta(head, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        title = m.group(1) if m else None
    return Page(
        final_url=url,
        site_name=doc.get("sitename") or _meta(head, "og:site_name") or urlsplit(url).hostname,
        title=title,
        author=doc.get("author") or _meta(head, "author", "article:author"),
        published_at=_date(doc.get("date") or _meta(head, "article:published_time")),
        text=doc.get("text") or "",
        excerpt=doc.get("description") or _meta(head, "og:description", "description"),
        image_url=doc.get("image") or _meta(head, "og:image", "twitter:image"),
    )


async def extract(url: str) -> Page:
    """Fetch ``url`` and return its readable content. Raises ``web.FetchError``."""
    post = x_status(url)
    if post:
        return (await _from_x(url, *post)).finish()
    from app.modules.pikirler.integrations import video_sites

    video = video_sites.youtube_id(url)
    if video:
        return (await video_sites.from_youtube(video)).finish()
    if video_sites.is_tiktok(url):
        return (await video_sites.from_tiktok(url)).finish()
    if video_sites.is_instagram_post(url):
        return (await video_sites.from_instagram(url)).finish()
    api_path = teswirler_api_path(url)
    if api_path:
        page = await _from_teswirler(url, api_path)
        if page is not None:
            return page.finish()
    got = await web.fetch(
        url,
        kinds=("text/html", "application/xhtml+xml", "text/plain"),
        max_bytes=get_settings().link_max_page_bytes,
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
    )
    if got.content_type == "text/plain":
        text = got.body.decode("utf-8", errors="replace")
        return Page(final_url=got.url, site_name=urlsplit(got.url).hostname, text=text).finish()
    page = await asyncio.to_thread(_extract_html, got.body, got.url)
    if page.image_url:
        page.image_url = _absolute(got.url, page.image_url)
    return page.finish()


def _absolute(base: str, url: str) -> str | None:
    from urllib.parse import urljoin

    full = urljoin(base, url)
    return full if full.startswith(("http://", "https://")) else None
