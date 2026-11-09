"""Client for the Teswirler (teswirler.com) API, used only to publish a copy of
a thought the owner explicitly ticked for it.

Protocol (as used by teswirler.com itself): ``POST /auth/login`` with
``X-Auth-Transport: cookie`` returns an access token (about 15 minutes) and
sets a refresh cookie; ``POST /auth/refresh`` renews it; ``POST /pikirler``
with ``{"content": ...}`` creates a post. Responses are wrapped in ``data``.

Nothing here logs request or response bodies: they hold credentials or the
owner's text.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import get_settings

USER_AGENT = "Pikirler/1.0 (+crosspost)"

_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Tests replace the network with an ``httpx.MockTransport``."""
    global _transport
    _transport = transport


class TeswirlerError(Exception):
    """Base class. ``code`` is stable and safe to show; ``message`` comes from
    Teswirler (never contains our request body)."""

    code = "teswirler_error"

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message[:300]
        self.status = status


class LoginRejected(TeswirlerError):
    code = "teswirler_login_failed"


class SessionExpired(TeswirlerError):
    """The stored session no longer works; the owner must connect again."""

    code = "teswirler_session_expired"


class Rejected(TeswirlerError):
    """Teswirler refused the post (validation, limits). Nothing was created."""

    code = "teswirler_rejected"


class Unavailable(TeswirlerError):
    """Teswirler could not be reached or answered 5xx/429 before accepting."""

    code = "teswirler_unavailable"


class OutcomeUnknown(TeswirlerError):
    """The request was sent but no answer came back: the post may exist."""

    code = "teswirler_outcome_unknown"


@dataclass
class Session:
    access_token: str
    refresh_token: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"access": self.access_token, "refresh": self.refresh_token, "cookies": self.cookies}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Session:
        cookies = value.get("cookies") or {}
        return cls(
            access_token=str(value.get("access") or ""),
            refresh_token=value.get("refresh") or None,
            cookies={str(k): str(v) for k, v in cookies.items()}
            if isinstance(cookies, dict)
            else {},
        )


@dataclass(frozen=True)
class Profile:
    user_id: str | None
    username: str | None
    display_name: str | None


@dataclass(frozen=True)
class Published:
    remote_id: str | None
    url: str


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        for key in ("message", "error"):
            value = body.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict) and isinstance(value.get("message"), str):
                return str(value["message"])
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
    return ""


def _data(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError as exc:
        raise Unavailable("unexpected response", status=response.status_code) from exc
    if not isinstance(body, dict) or "data" not in body:
        raise Unavailable("unexpected response", status=response.status_code)
    return body["data"]


class TeswirlerClient:
    def __init__(self, session: Session | None = None) -> None:
        settings = get_settings()
        self.api = settings.teswirler_api_url.rstrip("/")
        self.site = settings.teswirler_site_url.rstrip("/")
        self.session = session
        self._host = urlsplit(self.api).hostname or ""
        self._http = httpx.AsyncClient(
            timeout=settings.teswirler_timeout_seconds,
            transport=_transport,
            headers={"User-Agent": USER_AGENT, "Origin": self.site},
            follow_redirects=False,
        )
        if session:
            for name, value in session.cookies.items():
                self._http.cookies.set(name, value, domain=self._host)

    async def __aenter__(self) -> TeswirlerClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._http.aclose()

    def _cookie_dict(self) -> dict[str, str]:
        return {c.name: c.value or "" for c in self._http.cookies.jar}

    def _store(self, data: Any) -> None:
        token = data.get("token") if isinstance(data, dict) else None
        access = token.get("accessToken") if isinstance(token, dict) else None
        if not isinstance(access, str) or not access:
            raise Unavailable("unexpected authentication response")
        refresh = token.get("refreshToken") if isinstance(token, dict) else None
        previous = self.session.refresh_token if self.session else None
        self.session = Session(
            access_token=access,
            refresh_token=refresh if isinstance(refresh, str) and refresh else previous,
            cookies=self._cookie_dict(),
        )

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Network errors before the request was sent are ``Unavailable``."""
        try:
            return await self._http.request(method, self.api + path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise Unavailable("Teswirler is unreachable") from exc
        except httpx.HTTPError as exc:
            raise Unavailable("Teswirler did not answer") from exc

    async def login(self, identifier: str, password: str) -> Profile:
        response = await self._send(
            "POST",
            "/auth/login",
            json={"identifier": identifier, "password": password},
            headers={"X-Auth-Transport": "cookie"},
        )
        if response.status_code in (400, 401, 403, 404, 422):
            raise LoginRejected(_error_message(response), status=response.status_code)
        if response.status_code >= 300:
            raise Unavailable(_error_message(response), status=response.status_code)
        self._store(_data(response))
        return await self.me()

    async def refresh(self) -> None:
        refresh = self.session.refresh_token if self.session else None
        response = await self._send(
            "POST",
            "/auth/refresh",
            json={"refreshToken": refresh} if refresh else {},
            headers={"X-Auth-Transport": "cookie"},
        )
        if response.status_code in (400, 401, 403):
            raise SessionExpired(_error_message(response), status=response.status_code)
        if response.status_code >= 300:
            raise Unavailable(_error_message(response), status=response.status_code)
        self._store(_data(response))

    def _auth(self) -> dict[str, str]:
        if not self.session or not self.session.access_token:
            raise SessionExpired("not connected")
        return {"Authorization": f"Bearer {self.session.access_token}"}

    async def me(self) -> Profile:
        response = await self._send("GET", "/auth/me", headers=self._auth())
        if response.status_code == 401:
            await self.refresh()
            response = await self._send("GET", "/auth/me", headers=self._auth())
        if response.status_code == 401:
            raise SessionExpired(_error_message(response), status=401)
        if response.status_code >= 300:
            raise Unavailable(_error_message(response), status=response.status_code)
        data = _data(response)
        if not isinstance(data, dict):
            raise Unavailable("unexpected profile response")

        def text(key: str) -> str | None:
            value = data.get(key)
            return str(value)[:200] if value not in (None, "") else None

        return Profile(user_id=text("id"), username=text("username"), display_name=text("name"))

    async def _post_once(self, content: str) -> httpx.Response:
        try:
            return await self._http.post(
                self.api + "/pikirler", json={"content": content}, headers=self._auth()
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise Unavailable("Teswirler is unreachable") from exc
        except httpx.HTTPError as exc:  # sent, but the answer was lost
            raise OutcomeUnknown("no answer from Teswirler") from exc

    async def publish(self, content: str) -> Published:
        response = await self._post_once(content)
        if response.status_code == 401:  # rejected before anything was created
            await self.refresh()
            response = await self._post_once(content)
        if response.status_code == 401:
            raise SessionExpired(_error_message(response), status=401)
        if response.status_code in (429,) or response.status_code >= 500:
            # A 5xx after the request arrived might still have created the post.
            error: type[TeswirlerError] = (
                Unavailable if response.status_code in (429, 502, 503) else OutcomeUnknown
            )
            raise error(_error_message(response), status=response.status_code)
        if response.status_code >= 300:
            raise Rejected(_error_message(response), status=response.status_code)
        data = _data(response)
        remote_id = data.get("id") if isinstance(data, dict) else None
        remote = str(remote_id) if remote_id not in (None, "") else None
        url = f"{self.site}/pikirler" + (f"/{quote(remote, safe='')}" if remote else "")
        return Published(remote_id=remote, url=url)

    async def logout(self) -> None:
        refresh = self.session.refresh_token if self.session else None
        with contextlib.suppress(httpx.HTTPError):
            await self._http.post(
                self.api + "/auth/logout",
                json={"refreshToken": refresh} if refresh else {},
                headers={"X-Auth-Transport": "cookie"},
            )
