"""Fetching public web pages for the owner, safely.

The server sits on a private network next to PostgreSQL, Redis and RustFS, so
a link must never become a way to reach them (SSRF). Every hop is checked:

* only ``http``/``https`` on the default ports, no user:password@ in the URL;
* the host must resolve to public (global) addresses only, and the address
  actually connected to is checked again (DNS rebinding);
* redirects are followed by hand, each one checked the same way;
* proxies from the environment are ignored; size and time are capped.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import get_settings

USER_AGENT = "Mozilla/5.0 (compatible; Pikirler/1.0; personal link saver)"
MAX_REDIRECTS = 5

Resolver = Callable[[str, int], Awaitable[list[str]]]

_transport: httpx.AsyncBaseTransport | None = None
_resolver: Resolver | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


def set_resolver(resolver: Resolver | None) -> None:
    global _resolver
    _resolver = resolver


class FetchError(Exception):
    """``code`` is stable and shown to the owner; ``retryable`` decides whether
    the job tries again later."""

    code = "link_failed"
    retryable = False

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message or self.code)
        if code:
            self.code = code


class Blocked(FetchError):
    code = "link_blocked"


class PageNotFound(FetchError):
    code = "link_not_found"


class Refused(FetchError):
    code = "link_refused"


class Unsupported(FetchError):
    code = "link_unsupported"


class TooLarge(FetchError):
    code = "link_too_large"


class Unreachable(FetchError):
    code = "link_unreachable"
    retryable = True


@dataclass(frozen=True)
class Fetched:
    url: str
    content_type: str
    body: bytes


async def _system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Unreachable("the address does not resolve", code="link_dns") from exc
    return [str(info[4][0]) for info in infos]


def _check_ip(value: str) -> None:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError as exc:
        raise Blocked("not an IP address") from exc
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast:
        raise Blocked("private or reserved address")


async def check_url(url: str) -> str:
    """Raise unless ``url`` points at the public internet. Returns the host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise Blocked("only http and https links are saved")
    if parts.username or parts.password:
        raise Blocked("links with credentials are not fetched")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise Blocked("no host")
    try:
        port = parts.port
    except ValueError as exc:
        raise Blocked("bad port") from exc
    if port not in (None, 80, 443):
        raise Blocked("only default ports are fetched")
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        if "." not in host or host.endswith((".local", ".internal", ".lan", ".localhost")):
            raise Blocked("not a public host name") from None
        resolve = _resolver or _system_resolve
        addresses = await resolve(host, port or (443 if parts.scheme == "https" else 80))
    if not addresses:
        raise Unreachable("the address does not resolve", code="link_dns")
    for address in addresses:
        _check_ip(address)
    return host


def _check_peer(response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    addr = stream.get_extra_info("server_addr")
    if isinstance(addr, tuple) and addr:
        _check_ip(str(addr[0]))


async def fetch(
    url: str, *, kinds: tuple[str, ...], max_bytes: int, accept: str = "*/*"
) -> Fetched:
    """GET ``url`` and return its body if its content type starts with one of
    ``kinds``. Raises a ``FetchError`` subclass otherwise."""
    settings = get_settings()
    async with httpx.AsyncClient(
        transport=_transport,
        timeout=settings.link_fetch_timeout_seconds,
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "tk, ru;q=0.9, en;q=0.8",
        },
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            try:
                async with client.stream("GET", url) as response:
                    _check_peer(response)
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise Refused("redirect without a target")
                        url = urljoin(url, location)
                        continue
                    if status in (404, 410):
                        raise PageNotFound(f"HTTP {status}")
                    if status == 429 or status >= 500:
                        raise Unreachable(f"HTTP {status}")
                    if status >= 400:
                        raise Refused(f"HTTP {status}", code=f"link_http_{status}")
                    ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not ctype.startswith(kinds):
                        raise Unsupported(ctype or "no content type")
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise TooLarge("too large")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > max_bytes:
                            raise TooLarge("too large")
                    return Fetched(url=str(response.url), content_type=ctype, body=bytes(body))
            except httpx.TimeoutException as exc:
                raise Unreachable("timed out", code="link_timeout") from exc
            except httpx.HTTPError as exc:
                raise Unreachable("connection failed") from exc
    raise Refused("too many redirects", code="link_too_many_redirects")
