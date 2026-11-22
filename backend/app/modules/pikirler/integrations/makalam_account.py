"""Publishing a thought on makalam.com, used only when the owner ticked it.

makalam has no public API. Its pages are React Router routes over Django, and
Pikirler talks to them the way the site's own pages do:

* sign-in: ``GET /login/_.data`` gives the CSRF token (and cookie), then the
  sign-in form is posted to ``POST /login/_.data`` with ``csrfmiddlewaretoken``,
  the empty ``website`` bot trap, ``username`` and ``password``. A wrong login
  answers ``{errors, wrongLogin}``; success redirects. The root loader
  (``GET /_.data``) then reports the signed-in ``user``. The Django cookie
  session is what Pikirler keeps (encrypted); the password is not.
* a post: ``POST /posts/action`` with ``{intent: "create", key, parent: null,
  posts: [{content, images}]}``. Several ``posts`` make a chain ("zynjyr");
  each holds at most 500 characters, a chain at most 25 posts. ``key`` makes
  the request idempotent: the same key never creates a second post, and 409
  means the first request is still being handled.

Nothing here logs request or response bodies: they hold credentials or the
owner's text.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import get_settings
from app.modules.pikirler.integrations import remote, turbo_stream

USER_AGENT = "Mozilla/5.0 (compatible; Pikirler/1.0; +crosspost)"
POST_LIMIT = 500  # root loader ``postRules.post_max_length``
CHAIN_LIMIT = 25  # ``postRules.zynjyr_max_posts``
RETRY_DELAY = 2.0  # seconds between tries while the site answers 409 (in progress)
ATTEMPTS = 3

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests replace the network with an ``httpx.MockTransport``."""
    global _transport
    _transport = transport


class MakalamError(remote.RemoteError):
    code = "makalam_error"


class LoginRejected(MakalamError, remote.LoginRejected):
    code = "makalam_login_failed"


class SessionExpired(MakalamError, remote.SessionExpired):
    code = "makalam_session_expired"


class Rejected(MakalamError, remote.Rejected):
    code = "makalam_rejected"


class Unavailable(MakalamError, remote.Unavailable):
    code = "makalam_unavailable"


class OutcomeUnknown(MakalamError, remote.OutcomeUnknown):
    code = "makalam_outcome_unknown"


# --- splitting a long thought into a chain ----------------------------------------


def units(text: str) -> int:
    """Length as the site's composer counts it (UTF-16 code units): never
    less than the number of characters, so a part that fits here fits there."""
    return len(text.encode("utf-16-le")) // 2


def _cut(word: str, limit: int) -> Iterator[str]:
    piece = ""
    for char in word:
        if units(piece + char) > limit:
            yield piece
            piece = ""
        piece += char
    if piece:
        yield piece


def _pieces(text: str, limit: int) -> Iterator[str]:
    """Pieces of at most ``limit`` (trailing space kept), split at paragraphs,
    then sentences, then words, and only as a last resort inside a word."""
    for para in re.findall(r".+?(?:\n\s*\n|$)", text, re.S):
        if units(para.strip()) <= limit:
            yield para
            continue
        for sentence in re.findall(r".+?(?:[.!?…]+\s+|\n|$)", para, re.S):
            if units(sentence.strip()) <= limit:
                yield sentence
                continue
            for word in re.findall(r"\S+\s*", sentence):
                if units(word.strip()) <= limit:
                    yield word
                else:
                    yield from _cut(word.strip(), limit)


def split_chain(text: str, limit: int = POST_LIMIT) -> list[str]:
    """The posts of a chain that carry ``text`` without changing it."""
    text = text.strip()
    if units(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    current = ""
    for piece in _pieces(text, limit):
        if units((current + piece).strip()) <= limit:
            current += piece
            continue
        if current.strip():
            parts.append(current.strip())
        current = piece
    if current.strip():
        parts.append(current.strip())
    return parts


# --- the client ---------------------------------------------------------------------


@dataclass
class Session:
    cookies: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"cookies": self.cookies}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Session:
        cookies = value.get("cookies") or {}
        if not isinstance(cookies, dict):
            cookies = {}
        return cls(cookies={str(k): str(v) for k, v in cookies.items()})


@dataclass(frozen=True)
class Profile:
    user_id: str | None
    username: str | None
    display_name: str | None


@dataclass(frozen=True)
class Published:
    remote_id: str | None
    url: str
    parts: int


def _message(body: Any) -> str:
    """The site's own explanation, found like its pages find it: ``detail``,
    else the first text anywhere except under ``code``."""
    if isinstance(body, str):
        return body
    if isinstance(body, list):
        for item in body:
            found = _message(item)
            if found:
                return found
    elif isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
        for key, value in body.items():
            if key == "code":
                continue
            found = _message(value)
            if found:
                return found
    return ""


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class MakalamClient:
    def __init__(self, session: Session | None = None) -> None:
        settings = get_settings()
        self.base = settings.makalam_site_url.rstrip("/")
        self.session = session
        self._host = urlsplit(self.base).hostname or ""
        self._http = httpx.AsyncClient(
            timeout=settings.makalam_timeout_seconds,
            transport=_transport,
            headers={"User-Agent": USER_AGENT, "Origin": self.base},
            follow_redirects=False,
        )
        if session:
            for name, value in session.cookies.items():
                self._http.cookies.set(name, value, domain=self._host)

    async def __aenter__(self) -> MakalamClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    def _keep(self) -> None:
        self.session = Session(cookies={c.name: c.value or "" for c in self._http.cookies.jar})

    def _cookie(self, name: str) -> str | None:
        return next((c.value for c in self._http.cookies.jar if c.name == name and c.value), None)

    def _csrf_headers(self, referer: str) -> dict[str, str]:
        headers = {"Referer": self.base + referer}
        token = self._cookie("csrftoken")
        if token:
            headers["X-CSRFToken"] = token
        return headers

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """For requests that change nothing: any network error is ``Unavailable``."""
        try:
            return await self._http.request(method, self.base + path, **kwargs)
        except httpx.HTTPError as exc:
            raise Unavailable("makalam did not answer") from exc

    async def _loader(self, path: str) -> Any:
        data_path = "/_.data" if path == "/" else path.rstrip("/") + "/_.data"
        response = await self._send("GET", data_path, headers={"Accept": "text/x-script"})
        if response.status_code >= 300:
            raise Unavailable("unexpected answer", status=response.status_code)
        try:
            return turbo_stream.decode(response.text)
        except turbo_stream.Redirect as exc:
            if exc.target.startswith("/login"):
                raise SessionExpired("signed out") from exc
            raise Unavailable("unexpected redirect") from exc
        except ValueError as exc:
            raise Unavailable("unexpected answer") from exc

    async def login(self, identifier: str, password: str) -> Profile:
        self._http.cookies.clear()
        page = await self._loader("/login/")
        token = (turbo_stream.route_data(page, "routes/login") or {}).get("csrfToken")
        if not isinstance(token, str) or not token:
            token = self._cookie("csrftoken")
        if not token:
            raise Unavailable("no sign-in form")
        response = await self._send(
            "POST",
            "/login/_.data",
            data={
                "csrfmiddlewaretoken": token,
                "website": "",  # the form's bot trap stays empty
                "username": identifier,
                "password": password,
            },
            headers={"Accept": "text/x-script", **self._csrf_headers("/login/")},
        )
        if response.status_code == 429:
            raise Unavailable("too many attempts", status=429)
        if response.status_code >= 400:
            raise Unavailable("the sign-in form was refused", status=response.status_code)
        if response.status_code < 300:
            try:
                answer = turbo_stream.decode(response.text)
            except turbo_stream.Redirect:
                answer = None  # signed in
            except ValueError as exc:
                raise Unavailable("unexpected answer") from exc
            failure = turbo_stream.find(answer, {"wrongLogin", "errors"})
            if failure is not None:
                if failure.get("limit") is True:
                    raise Unavailable(_message(failure.get("errors")) or "too many attempts")
                if failure.get("wrongLogin") or failure.get("errors"):
                    raise LoginRejected(_message(failure.get("errors")))
        try:
            profile = await self.me()
        except SessionExpired as exc:
            raise LoginRejected("") from exc
        self._keep()
        return profile

    async def me(self) -> Profile:
        root = await self._loader("/")
        user = (turbo_stream.route_data(root, "root") or {}).get("user")
        if not isinstance(user, dict) or not user.get("username"):
            raise SessionExpired("signed out")

        def text(key: str) -> str | None:
            value = user.get(key)
            return str(value)[:200] if value not in (None, "") else None

        return Profile(user_id=text("id"), username=text("username"), display_name=text("name"))

    async def _create(self, body: dict[str, Any], *, first: bool) -> httpx.Response:
        try:
            return await self._http.post(
                self.base + "/posts/action",
                json=body,
                headers={"Accept": "application/json", **self._csrf_headers("/")},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            if first:  # never reached the site: nothing was created
                raise Unavailable("makalam is unreachable") from exc
            raise OutcomeUnknown("no answer from makalam") from exc
        except httpx.HTTPError as exc:  # sent, but the answer was lost
            raise OutcomeUnknown("no answer from makalam") from exc

    async def publish(self, parts: list[str], key: str) -> Published:
        """Publish ``parts`` as one post or a chain. Safe to call again with
        the same ``key``: the site creates the posts only once."""
        if not self.session or not self.session.cookies:
            raise SessionExpired("not connected")
        body = {
            "intent": "create",
            "key": key,
            "parent": None,
            "posts": [{"content": part, "images": []} for part in parts],
        }
        for attempt in range(1, ATTEMPTS + 1):
            response = await self._create(body, first=attempt == 1)
            status = response.status_code
            answer = _json(response)
            code = answer.get("code") if isinstance(answer, dict) else None
            if status in (200, 201):
                self._keep()
                return self._published(answer, len(parts))
            if status == 409 and attempt < ATTEMPTS:  # the same key is still being handled
                await asyncio.sleep(RETRY_DELAY)
                continue
            if status == 409:
                raise OutcomeUnknown("makalam is still handling the post", status=409)
            # The site's own rule: 401, or 403 without a code, means signed out.
            if status == 401 or (status == 403 and not code):
                raise SessionExpired(_message(answer), status=status)
            if status in (429, 502, 503):
                raise Unavailable(_message(answer), status=status)
            if status >= 500:
                raise OutcomeUnknown(_message(answer), status=status)
            raise Rejected(_message(answer) or str(code or ""), status=status)
        raise OutcomeUnknown("no answer from makalam")  # pragma: no cover

    def _published(self, answer: Any, parts: int) -> Published:
        posts = answer.get("posts") if isinstance(answer, dict) else None
        first = posts[0] if isinstance(posts, list) and posts else None
        first = first if isinstance(first, dict) else {}
        remote_id = first.get("id")
        path = first.get("path")
        if isinstance(path, str) and path.startswith("/"):
            url = self.base + path
        elif isinstance(path, str) and path.startswith("https://"):
            url = path
        else:
            author = first.get("author")
            username = author.get("username") if isinstance(author, dict) else None
            url = (
                f"{self.base}/@{quote(str(username), safe='')}/"
                + (f"post/{quote(str(remote_id), safe='')}" if remote_id is not None else "")
                if username
                else self.base + "/"
            )
        return Published(
            remote_id=str(remote_id) if remote_id not in (None, "") else None, url=url, parts=parts
        )

    async def logout(self) -> None:
        token = self._cookie("csrftoken")
        if not token:
            return
        with contextlib.suppress(httpx.HTTPError):
            await self._http.post(
                self.base + "/logout/",
                data={"csrfmiddlewaretoken": token},
                headers=self._csrf_headers("/"),
            )
