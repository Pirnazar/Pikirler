"""Cross-posting to Teswirler, against a fake Teswirler API (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from sqlalchemy import select, update

from app.core.database import get_database
from app.modules.pikirler.integrations import teswirler
from app.modules.pikirler.models import Crosspost, ExternalAccount
from tests.conftest import _client, new_thought
from tests.helpers import run_jobs

PASSWORD = "teswirler-pass-123"


@dataclass
class FakeTeswirler:
    access: str = "access-1"
    refresh_cookie: str = "refresh-cookie-1"
    expired: set[str] = field(default_factory=set)
    publish_mode: str = "ok"  # ok | timeout | reject
    posts: list[str] = field(default_factory=list)
    logins: int = 0
    refreshes: int = 0
    logouts: int = 0
    counter: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/login":
            body = json.loads(request.content)
            self.logins += 1
            if body != {"identifier": "pirnazar", "password": PASSWORD}:
                return httpx.Response(401, json={"message": "Invalid credentials"})
            return httpx.Response(
                200,
                json={"data": {"token": {"accessToken": self.access}}},
                headers={"Set-Cookie": f"refreshToken={self.refresh_cookie}; Path=/; HttpOnly"},
            )
        if path == "/auth/refresh":
            self.refreshes += 1
            if f"refreshToken={self.refresh_cookie}" not in request.headers.get("cookie", ""):
                return httpx.Response(401, json={"message": "No session"})
            self.access = f"access-{self.refreshes + 1}"
            return httpx.Response(200, json={"data": {"token": {"accessToken": self.access}}})
        if path == "/auth/logout":
            self.logouts += 1
            return httpx.Response(200, json={"data": {}})
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token != self.access or token in self.expired:
            return httpx.Response(401, json={"message": "Unauthorized"})
        if path == "/auth/me":
            return httpx.Response(
                200, json={"data": {"id": "u1", "username": "pirnazar", "name": "Pirnazar"}}
            )
        if path == "/pikirler" and request.method == "POST":
            content = json.loads(request.content)["content"]
            if self.publish_mode == "timeout":
                self.posts.append(content)
                raise httpx.ReadTimeout("lost", request=request)
            if self.publish_mode == "reject":
                return httpx.Response(400, json={"message": "Pikir gaty uzyn"})
            self.posts.append(content)
            self.counter += 1
            return httpx.Response(
                201, json={"data": {"id": f"p{self.counter}", "content": content}}
            )
        return httpx.Response(404, json={"message": "nope"})


@pytest.fixture
def fake() -> Iterator[FakeTeswirler]:
    f = FakeTeswirler()
    teswirler.set_transport(httpx.MockTransport(f.handler))
    yield f
    teswirler.set_transport(None)


async def _connect(client: httpx.AsyncClient) -> httpx.Response:
    return await client.put(
        "/crosspost/teswirler", json={"identifier": "pirnazar", "password": PASSWORD}
    )


async def _rows() -> list[Crosspost]:
    async with get_database().sessionmaker() as db:
        return list((await db.execute(select(Crosspost))).scalars())


async def test_connect_keeps_no_password(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    assert (await client.get("/crosspost/teswirler")).json()["connected"] is False
    resp = await _connect(client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True
    assert body["username"] == "pirnazar"
    assert body["profile_url"] == "https://teswirler.com/@pirnazar"
    async with get_database().sessionmaker() as db:
        account = (await db.execute(select(ExternalAccount))).scalar_one()
    # Only an encrypted session is stored: no password, no readable token.
    for secret in (PASSWORD, fake.access, fake.refresh_cookie):
        assert secret not in account.credentials


async def test_wrong_teswirler_password(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    resp = await client.put(
        "/crosspost/teswirler", json={"identifier": "pirnazar", "password": "wrong"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "teswirler_login_failed"
    assert (await client.get("/crosspost/teswirler")).json()["connected"] is False


async def test_crosspost_needs_a_connected_account(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    resp = await client.post("/thoughts", json={"content": "Salam", "crosspost": ["teswirler"]})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "teswirler_not_connected"
    # The thought itself was not created either: nothing half-done.
    assert (await client.get("/thoughts")).json()["items"] == []


async def test_thought_without_tick_stays_private(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    created = await new_thought(client, "Diňe meniňki")
    assert created["crossposts"] == []
    await run_jobs()
    assert fake.posts == []


async def test_crosspost_is_sent_once(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    await _connect(client)
    created = await new_thought(client, "Salam, Teswirler!", crosspost=["teswirler"])
    assert [c["status"] for c in created["crossposts"]] == ["pending"]
    await run_jobs()
    await run_jobs()
    assert fake.posts == ["Salam, Teswirler!"]
    thought = (await client.get(f"/thoughts/{created['id']}")).json()
    [shared] = thought["crossposts"]
    assert shared["status"] == "sent"
    assert shared["url"] == "https://teswirler.com/pikirler/p1"
    again = await client.post(f"/thoughts/{created['id']}/crossposts/teswirler")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "crosspost_already_sent"


async def test_expired_token_is_refreshed_and_kept(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    fake.expired.add(fake.access)
    created = await new_thought(client, "Täze token", crosspost=["teswirler"])
    await run_jobs()
    assert fake.refreshes == 1
    assert fake.posts == ["Täze token"]
    # The refreshed session was saved: the next post needs no refresh.
    await new_thought(client, "Ikinji", crosspost=["teswirler"])
    await run_jobs()
    assert fake.refreshes == 1
    assert fake.posts == ["Täze token", "Ikinji"]
    assert (await client.get(f"/thoughts/{created['id']}/crossposts")).json()[0]["status"] == "sent"


async def test_lost_answer_is_unknown_and_not_resent(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    fake.publish_mode = "timeout"
    created = await new_thought(client, "Belki gitdi", crosspost=["teswirler"])
    await run_jobs()
    await run_jobs()
    assert fake.posts == ["Belki gitdi"]  # exactly one attempt
    [shared] = (await client.get(f"/thoughts/{created['id']}/crossposts")).json()
    assert shared["status"] == "unknown"
    assert shared["error_code"] == "teswirler_outcome_unknown"
    # The owner checked Teswirler and asks again.
    fake.publish_mode = "ok"
    retried = await client.post(f"/thoughts/{created['id']}/crossposts/teswirler")
    assert retried.status_code == 200
    assert retried.json()[0]["status"] == "pending"
    await run_jobs()
    assert fake.posts == ["Belki gitdi", "Belki gitdi"]
    assert (await client.get(f"/thoughts/{created['id']}/crossposts")).json()[0]["status"] == "sent"


async def test_rejected_post_reports_teswirler_message(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    fake.publish_mode = "reject"
    created = await new_thought(client, "Uzyn", crosspost=["teswirler"])
    await run_jobs()
    [shared] = (await client.get(f"/thoughts/{created['id']}/crossposts")).json()
    assert shared["status"] == "failed"
    assert shared["error_code"] == "teswirler_rejected"
    assert shared["error_message"] == "Pikir gaty uzyn"


async def test_crash_while_sending_never_resends(
    client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    created = await new_thought(client, "Ýarym", crosspost=["teswirler"])
    async with get_database().sessionmaker() as db:
        await db.execute(update(Crosspost).values(status="sending"))
        await db.commit()
    await run_jobs()
    assert fake.posts == []
    assert (await _rows())[0].status == "unknown"
    assert created["id"]


async def test_share_existing_thought_later(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    await _connect(client)
    created = await new_thought(client, "Soňra paýlaşaryn")
    resp = await client.post(f"/thoughts/{created['id']}/crossposts/teswirler")
    assert resp.status_code == 200
    await run_jobs()
    assert fake.posts == ["Soňra paýlaşaryn"]


async def test_empty_text_cannot_be_shared(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    await _connect(client)
    resp = await client.post(
        "/thoughts", json={"content": "x", "status": "draft", "crosspost": ["teswirler"]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "crosspost_not_published"


async def test_disconnect_ends_the_session(client: httpx.AsyncClient, fake: FakeTeswirler) -> None:
    await _connect(client)
    resp = await client.delete("/crosspost/teswirler")
    assert resp.status_code == 200
    assert resp.json()["connected"] is False
    assert fake.logouts == 1
    created = await new_thought(client, "Soň")
    resp = await client.post(f"/thoughts/{created['id']}/crossposts/teswirler")
    assert resp.status_code == 409


async def test_others_cannot_share_my_thought(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, fake: FakeTeswirler
) -> None:
    await _connect(client)
    await _connect(other_client)
    created = await new_thought(client, "Meniňki")
    resp = await other_client.post(f"/thoughts/{created['id']}/crossposts/teswirler")
    assert resp.status_code == 404
    await run_jobs()
    assert fake.posts == []


async def test_service_tokens_cannot_crosspost(client, owner, db, fake) -> None:  # type: ignore[no-untyped-def]
    from app.modules.pikirler.services.auth import create_service_client

    await _connect(client)
    _, token = await create_service_client(
        db, owner.id, "jemshit", ["thoughts:write", "thoughts:read"], "jemshit"
    )
    async with _client() as svc:
        svc.headers["Authorization"] = f"Bearer {token}"
        resp = await svc.post("/thoughts", json={"content": "Hi", "crosspost": ["teswirler"]})
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "crosspost_owner_only"
        assert (await svc.get("/crosspost/teswirler")).status_code == 403
    assert fake.posts == []
