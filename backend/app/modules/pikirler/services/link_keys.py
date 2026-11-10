"""One saved copy per page: the key two links of the same page share.

``canonical_url`` maps the many spellings of a link (share parameters, short
domains, mobile hosts, a renamed X account) to one address. It is pure and
dependency-free so migrations can use it too.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

# Query parameters that only say where a link was shared from.
TRACKING = {
    "fbclid", "gclid", "dclid", "msclkid", "igsh", "igshid", "si", "feature", "ref", "ref_src",
    "ref_url", "s", "t_src", "mc_cid", "mc_eid", "_ga", "yclid", "spm", "share_id",
}  # fmt: skip
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _youtube(host: str, segments: list[str], query: str) -> str | None:
    video: str | None = None
    if host == "youtu.be" and segments:
        video = segments[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if segments[:1] == ["watch"]:
            values = [v for k, v in parse_qsl(query) if k == "v"]
            video = values[0] if values else None
        elif len(segments) >= 2 and segments[0] in ("shorts", "live", "embed", "v"):
            video = segments[1]
    if video and _YT_ID.match(video):
        return f"https://www.youtube.com/watch?v={video}"
    return None


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().rstrip(".")
    bare = host.removeprefix("www.")
    segments = [unquote(s) for s in parts.path.split("/") if s]

    youtube = _youtube(bare, segments, parts.query)
    if youtube:
        return youtube
    third = segments[2] if len(segments) >= 3 else ""
    if (
        bare in {"x.com", "twitter.com", "mobile.twitter.com", "mobile.x.com"}
        and segments[1:2] in (["status"], ["statuses"])
        and third.isdigit()
    ):
        return f"https://x.com/i/status/{third}"
    if bare in {"tiktok.com", "m.tiktok.com"} and segments[1:2] == ["video"] and third.isdigit():
        return f"https://www.tiktok.com/video/{third}"
    if (
        bare in {"instagram.com", "m.instagram.com"}
        and len(segments) >= 2
        and segments[0] in ("p", "reel", "reels", "tv")
    ):
        return f"https://www.instagram.com/p/{quote(segments[1], safe='')}/"
    if bare == "teswirler.com":
        return "https://teswirler.com/" + "/".join(quote(s, safe="@") for s in segments)

    netloc = host
    if parts.port and not (
        (scheme == "https" and parts.port == 443) or (scheme == "http" and parts.port == 80)
    ):
        netloc += f":{parts.port}"
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING and not k.lower().startswith("utm_")
        ]
    )
    return urlunsplit((scheme, netloc, path, query, ""))
