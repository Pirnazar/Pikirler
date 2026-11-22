"""Cross-posting to makalam.com, against a fake site (httpx.MockTransport) that
answers like the real one: turbo-stream route data, a Django cookie session,
``/posts/action`` with an idempotency key."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.core.database import get_database
from app.modules.pikirler.integrations import makalam_account, turbo_stream
from app.modules.pikirler.integrations.makalam_account import split_chain, units
from app.modules.pikirler.models import Crosspost, ExternalAccount
from tests.conftest import new_thought
from tests.helpers import jpeg_with_gps, multipart_files, run_jobs, upload

PASSWORD = "makalam-pass-456"


def turbo(value: Any) -> str:
    """Encode like React Router's turbo-stream (the parts makalam uses)."""
    table: list[Any] = []

    def add(v: Any) -> int:
        if v is None:
            return -5
        index = len(table)
        table.append(None)
        if isinstance(v, dict):
            table[index] = {f"_{add(k)}": add(x) for k, x in v.items()}
        elif isinstance(v, list):
            table[index] = [add(x) for x in v]
        else:
            table[index] = v
        return index

    add(value)
    return json.dumps(table) + "\n"


def turbo_redirect(target: str) -> str:
    return (
        json.dumps(
            [["SingleFetchRedirect", 1], {"_2": 3, "_4": 5}, "redirect", target, "status", 302]
        )
        + "\n"
    )


@dataclass
class FakeMakalam:
    sessions: set[str] = field(default_factory=lambda: set())
    mode: str = "ok"  # ok | timeout | busy | paused
    posts: list[list[str]] = field(default_factory=list)
    images: list[list[Any]] = field(default_factory=list)
    uploads: list[tuple[str, str, bytes]] = field(default_factory=list)
    busy_uploads: int = 0
    by_key: dict[str, int] = field(default_factory=dict)
    calls: int = 0
    logins: int = 0
    logouts: int = 0
    counter: int = 0

    def _session(self, request: httpx.Request) -> str | None:
        cookies = dict(
            part.strip().split("=", 1)
            for part in request.headers.get("cookie", "").split(";")
            if "=" in part
        )
        sid = cookies.get("sessionid")
        return sid if sid in self.sessions else None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        script = {"content-type": "text/x-script; charset=utf-8"}
        if path == "/login/_.data" and method == "GET":
            return httpx.Response(
                200,
                text=turbo(
                    {
                        "root": {"data": {"user": None}},
                        "routes/login": {"data": {"csrfToken": "tok-1"}},
                    }
                ),
                headers={**script, "set-cookie": "csrftoken=ck-1; Path=/"},
            )
        if path == "/login/_.data" and method == "POST":
            self.logins += 1
            form = {
                k: v[0]
                for k, v in parse_qs(request.content.decode(), keep_blank_values=True).items()
            }
            ok = (
                form.get("csrfmiddlewaretoken") == "tok-1"
                and form.get("website") == ""
                and form.get("username") == "pirnazar"
                and form.get("password") == PASSWORD
                and request.headers.get("x-csrftoken") == "ck-1"
                and request.headers.get("origin") == "https://makalam.com"
            )
            if not ok:
                failure = {
                    "errors": {"non_field_errors": ["Dogry ulanyjy ady we paroly giriziň."]},
                    "wrongLogin": True,
                    "limit": False,
                }
                return httpx.Response(200, text=turbo({"data": failure}), headers=script)
            sid = f"sess-{self.logins}"
            self.sessions.add(sid)
            return httpx.Response(
                202,
                text=turbo_redirect("/"),
                headers={**script, "set-cookie": f"sessionid={sid}; Path=/; HttpOnly"},
            )
        if path == "/_.data":
            user = (
                {"id": 7, "username": "pirnazar", "name": "Pirnazar"}
                if self._session(request)
                else None
            )
            return httpx.Response(
                200,
                text=turbo({"root": {"data": {"user": user}}, "routes/home": {"data": {}}}),
                headers=script,
            )
        if path == "/logout/" and method == "POST":
            self.logouts += 1
            return httpx.Response(302, headers={"location": "/"})
        if path == "/posts/upload" and method == "POST":
            if not self._session(request):
                return httpx.Response(403, json={"detail": "Giriň"})
            if self.busy_uploads:
                self.busy_uploads -= 1
                return httpx.Response(503, json={"detail": "busy"})
            [(name, filename, mime, data)] = multipart_files(request)
            assert name == "upload"
            self.uploads.append((filename, mime, data))
            return httpx.Response(201, json={"id": 900 + len(self.uploads)})
        if path == "/posts/action" and method == "POST":
            self.calls += 1
            if not self._session(request):
                return httpx.Response(403, json={"detail": "Giriň"})
            body = json.loads(request.content)
            assert body["intent"] == "create" and body["parent"] is None
            key = body["key"]
            contents = [p["content"] for p in body["posts"]]
            self.images.append([[i["image"] for i in p["images"]] for p in body["posts"]])
            if self.mode == "paused":
                return httpx.Response(
                    403, json={"code": "posting_paused", "detail": "Ýazmak wagtlaýyn togtadyldy"}
                )
            if self.mode == "busy":
                self.mode = "ok"
                self.by_key.setdefault(key, self._create(contents))
                return httpx.Response(409, json={"code": "in_progress"})
            if key not in self.by_key:  # the site creates posts once per key
                self.by_key[key] = self._create(contents)
            if self.mode == "timeout":
                raise httpx.ReadTimeout("lost", request=request)
            first = self.by_key[key]
            return httpx.Response(
                201,
                json={
                    "posts": [
                        {"id": first + i, "path": f"/@pirnazar/post/{first + i}"}
                        for i in range(len(contents))
                    ]
                },
                headers={"set-cookie": "csrftoken=ck-2; Path=/"},
            )
        return httpx.Response(404, json={"detail": "nope"})

    def _create(self, contents: list[str]) -> int:
        self.posts.append(contents)
        first = self.counter + 1
        self.counter += len(contents)
        return first


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMakalam]:
    f = FakeMakalam()
    makalam_account.set_transport(httpx.MockTransport(f.handler))
    monkeypatch.setattr(makalam_account, "RETRY_DELAY", 0)
    yield f
    makalam_account.set_transport(None)


async def _connect(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.put(
        "/crosspost/makalam", json={"identifier": "pirnazar", "password": password}
    )


async def _shared(client: httpx.AsyncClient, thought_id: str) -> dict[str, Any]:
    [row] = (await client.get(f"/thoughts/{thought_id}/crossposts")).json()
    return row  # type: ignore[no-any-return]


# --- the turbo-stream decoder and the chain -----------------------------------------


def test_turbo_stream_decoding() -> None:
    root = turbo_stream.decode(turbo({"root": {"data": {"user": None, "rules": [1, "a", True]}}}))
    assert root == {"root": {"data": {"user": None, "rules": [1, "a", True]}}}
    with pytest.raises(turbo_stream.Redirect) as redirect:
        turbo_stream.decode(turbo_redirect("/login/?next=/"))
    assert redirect.value.target == "/login/?next=/"
    with pytest.raises(ValueError):
        turbo_stream.decode("<html>")


def test_long_text_becomes_a_chain_without_losing_words() -> None:
    paragraphs = ["Birinji abzas. " * 33, "Ikinji abzas, gysga.", "Üçünji. " * 70]
    text = "\n\n".join(p.strip() for p in paragraphs)
    parts = split_chain(text)
    assert len(parts) > 1
    assert all(0 < units(p) <= 500 for p in parts)
    assert " ".join(" ".join(parts).split()) == " ".join(text.split())
    assert parts[1].startswith("Ikinji abzas")  # split at the paragraph, not inside it


def test_chain_counts_like_the_site_and_cuts_huge_words() -> None:
    assert split_chain("salam") == ["salam"]
    assert split_chain("   ") == []
    emoji = "🙂" * 300  # 600 UTF-16 units: two posts, though 300 characters
    assert [len(p) for p in split_chain(emoji)] == [250, 50]
    word = "a" * 1200
    assert [len(p) for p in split_chain(word)] == [500, 500, 200]


# --- the account ------------------------------------------------------------------


async def test_connect_keeps_no_password(client: httpx.AsyncClient, fake: FakeMakalam) -> None:
    assert (await client.get("/crosspost/makalam")).json() == {
        "site": "makalam",
        "connected": False,
        "username": None,
        "display_name": None,
        "profile_url": None,
        "connected_at": None,
    }
    resp = await _connect(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["connected"], body["username"], body["display_name"]) == (
        True,
        "pirnazar",
        "Pirnazar",
    )
    assert body["profile_url"] == "https://makalam.com/@pirnazar/"
    async with get_database().sessionmaker() as db:
        account = (await db.execute(select(ExternalAccount))).scalar_one()
    assert account.provider == "makalam"
    for secret in (PASSWORD, "sess-1", "ck-1"):
        assert secret not in account.credentials
    # Teswirler is a separate account.
    assert (await client.get("/crosspost/teswirler")).json()["connected"] is False


async def test_wrong_makalam_password(client: httpx.AsyncClient, fake: FakeMakalam) -> None:
    resp = await _connect(client, "wrong")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "makalam_login_failed"
    assert (await client.get("/crosspost/makalam")).json()["connected"] is False


async def test_crosspost_needs_a_connected_makalam(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    resp = await client.post("/thoughts", json={"content": "Salam", "crosspost": ["makalam"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "makalam_not_connected"
    assert (await client.get("/thoughts")).json()["items"] == []


async def test_disconnect_signs_out_on_makalam(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    resp = await client.delete("/crosspost/makalam")
    assert resp.json()["connected"] is False
    assert fake.logouts == 1


# --- publishing --------------------------------------------------------------------


async def test_thought_is_published_once(client: httpx.AsyncClient, fake: FakeMakalam) -> None:
    await _connect(client)
    created = await new_thought(client, "Salam, makalam!", crosspost=["makalam"])
    assert [c["target"] for c in created["crossposts"]] == ["makalam"]
    await run_jobs()
    await run_jobs()
    assert fake.posts == [["Salam, makalam!"]]
    shared = await _shared(client, created["id"])
    assert (shared["status"], shared["url"]) == ("sent", "https://makalam.com/@pirnazar/post/1")
    again = await client.post(f"/thoughts/{created['id']}/crossposts/makalam")
    assert again.json()["error"]["code"] == "crosspost_already_sent"
    # The cookies the site renewed were kept for the next post.
    async with get_database().sessionmaker() as db:
        from app.core.secretbox import unseal

        account = (await db.execute(select(ExternalAccount))).scalar_one()
        assert (
            unseal(account.credentials, "external-account/makalam")["cookies"]["csrftoken"]
            == "ck-2"
        )


async def test_long_thought_goes_as_a_chain(client: httpx.AsyncClient, fake: FakeMakalam) -> None:
    await _connect(client)
    text = "\n\n".join(f"{n}-nji abzas. " + "Söz " * 100 for n in range(1, 4))
    created = await new_thought(client, text, crosspost=["makalam"])
    await run_jobs()
    [chain] = fake.posts
    assert len(chain) == 3 and all(units(p) <= 500 for p in chain)
    assert (await _shared(client, created["id"]))["url"] == "https://makalam.com/@pirnazar/post/1"


async def test_too_long_for_makalam_is_refused_up_front(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    text = "\n\n".join("Söz " * 120 for _ in range(30))  # 30 posts: more than a chain holds
    resp = await client.post("/thoughts", json={"content": text, "crosspost": ["makalam"]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "makalam_too_long"
    assert fake.calls == 0


async def test_lost_answer_then_retry_makes_no_second_post(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    fake.mode = "timeout"
    created = await new_thought(client, "Belki gitdi", crosspost=["makalam"])
    await run_jobs()
    await run_jobs()
    shared = await _shared(client, created["id"])
    assert (shared["status"], shared["error_code"]) == ("unknown", "makalam_outcome_unknown")
    assert fake.calls == 1  # never resent by itself
    fake.mode = "ok"
    assert (await client.post(f"/thoughts/{created['id']}/crossposts/makalam")).status_code == 200
    await run_jobs()
    # The same key: makalam answered with the post it already had.
    assert fake.posts == [["Belki gitdi"]]
    assert (await _shared(client, created["id"]))["status"] == "sent"


async def test_post_still_in_progress_is_awaited(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    fake.mode = "busy"
    created = await new_thought(client, "Garaşýan", crosspost=["makalam"])
    await run_jobs()
    assert fake.calls == 2 and fake.posts == [["Garaşýan"]]
    assert (await _shared(client, created["id"]))["status"] == "sent"


async def test_refusal_reports_makalam_message(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    fake.mode = "paused"
    created = await new_thought(client, "Togtadyldy", crosspost=["makalam"])
    await run_jobs()
    shared = await _shared(client, created["id"])
    assert (shared["status"], shared["error_code"]) == ("failed", "makalam_rejected")
    assert shared["error_message"] == "Ýazmak wagtlaýyn togtadyldy"


async def test_expired_session_asks_to_connect_again(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    await _connect(client)
    fake.sessions.clear()  # makalam ended the session
    created = await new_thought(client, "Köne sessiýa", crosspost=["makalam"])
    await run_jobs()
    shared = await _shared(client, created["id"])
    assert (shared["status"], shared["error_code"]) == ("failed", "makalam_session_expired")
    assert fake.posts == []


async def test_share_existing_thought_later(client: httpx.AsyncClient, fake: FakeMakalam) -> None:
    await _connect(client)
    created = await new_thought(client, "Soňra")
    resp = await client.post(f"/thoughts/{created['id']}/crossposts/makalam")
    assert resp.status_code == 200
    await run_jobs()
    assert fake.posts == [["Soňra"]]
    async with get_database().sessionmaker() as db:
        [row] = (await db.execute(select(Crosspost))).scalars()
    assert row.target == "makalam" and row.status == "sent"


async def test_pictures_go_with_the_first_post_and_videos_stay(
    client: httpx.AsyncClient, fake: FakeMakalam
) -> None:
    ids = []
    for data, name, mime in [
        (jpeg_with_gps(), "a.jpg", "image/jpeg"),
        (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000, "v.mp4", "video/mp4"),
        (jpeg_with_gps(1), "b.jpg", "image/jpeg"),
    ]:
        resp = await upload(client, data, name, mime)
        assert resp.status_code == 200, resp.text
        ids.append(resp.json()["id"])
    await _connect(client)
    fake.busy_uploads = 1  # makalam asks to try the first picture again
    text = "\n\n".join(f"{n}-nji abzas. " + "Söz " * 100 for n in range(1, 3))
    await new_thought(client, text, attachment_ids=ids, crosspost=["makalam"])
    await run_jobs()
    assert [(n, m) for n, m, _ in fake.uploads] == [
        ("a.jpg", "image/jpeg"),
        ("b.jpg", "image/jpeg"),
    ]
    assert b"SecretCam" not in fake.uploads[0][2]
    # A chain of two posts: the pictures are on the first one.
    assert fake.images == [[[901, 902], []]]
