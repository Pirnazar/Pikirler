from __future__ import annotations

import httpx
from sqlalchemy import select

from app.modules.pikirler.models import AuthSession
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, _client, login, new_thought


async def test_api_requires_authentication(anon: httpx.AsyncClient, owner: object) -> None:
    for method, path in [
        ("GET", "/thoughts"),
        ("POST", "/thoughts"),
        ("GET", "/timeline"),
        ("GET", "/tags"),
        ("GET", "/auth/me"),
        ("GET", "/settings"),
    ]:
        resp = await anon.request(method, path, json={})
        assert resp.status_code == 401, (method, path)
        assert resp.json()["error"]["code"] == "unauthorized"


async def test_no_public_registration_endpoint(anon: httpx.AsyncClient) -> None:
    for path in ("/auth/register", "/auth/signup", "/owners"):
        assert (await anon.post(path, json={})).status_code in (404, 405)


async def test_login_sets_httponly_cookie_and_csrf(anon: httpx.AsyncClient, owner: object) -> None:
    resp = await anon.post("/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert resp.status_code == 200
    cookies = resp.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("pikirler_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("pikirler_csrf="))
    assert "HttpOnly" in session_cookie and "SameSite=lax" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert resp.json()["csrf_token"]


async def test_wrong_password_and_unknown_email_look_the_same(
    anon: httpx.AsyncClient, owner: object
) -> None:
    a = await anon.post("/auth/login", json={"email": OWNER_EMAIL, "password": "nope"})
    b = await anon.post("/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["code"] == b.json()["error"]["code"] == "invalid_credentials"


async def test_login_rate_limit_per_account(anon: httpx.AsyncClient, owner: object) -> None:
    codes = [
        (await anon.post("/auth/login", json={"email": OWNER_EMAIL, "password": "bad"})).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429
    # Even the right password is refused while the window lasts.
    resp = await anon.post("/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


async def test_csrf_required_for_unsafe_methods(client: httpx.AsyncClient) -> None:
    token = client.headers.pop("X-CSRF-Token")
    resp = await client.post("/thoughts", json={"content": "x"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = "forged"
    assert (await client.post("/thoughts", json={"content": "x"})).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert (await client.post("/thoughts", json={"content": "x"})).status_code == 201
    # Safe methods do not need it.
    client.headers.pop("X-CSRF-Token")
    assert (await client.get("/thoughts")).status_code == 200


async def test_logout_revokes_session(client: httpx.AsyncClient) -> None:
    cookie = client.cookies.get("pikirler_session")
    assert (await client.post("/auth/logout")).status_code == 200
    replay = _client()
    replay.cookies.set("pikirler_session", cookie)
    assert (await replay.get("/thoughts")).status_code == 401
    await replay.aclose()


async def test_revoked_session_from_session_list(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    second = await login(OWNER_EMAIL, OWNER_PASSWORD)
    sessions = (await client.get("/auth/sessions")).json()
    assert len(sessions) == 2
    other = next(s for s in sessions if not s["current"])
    assert (await client.delete(f"/auth/sessions/{other['id']}")).status_code == 200
    assert (await second.get("/thoughts")).status_code == 401
    assert (await client.get("/thoughts")).status_code == 200
    stored = (await db.execute(select(AuthSession))).scalars().all()
    # Tokens are stored only as keyed digests.
    assert all(len(s.token_hash) == 64 for s in stored)
    assert second.cookies.get("pikirler_session") not in {s.token_hash for s in stored}
    await second.aclose()


async def test_magic_word_locks_pikirler_until_unlocked(client: httpx.AsyncClient) -> None:
    resp = await client.put(
        "/auth/magic-word", json={"password": OWNER_PASSWORD, "magic_word": "Ýyldyrym"}
    )
    assert resp.status_code == 200
    # The session that set it stays usable; a new one starts locked.
    fresh = await login(OWNER_EMAIL, OWNER_PASSWORD)
    me = (await fresh.get("/auth/me")).json()
    assert me["magic_word_enabled"] is True and me["unlocked"] is False
    locked = await fresh.get("/thoughts")
    assert locked.status_code == 423
    assert locked.json()["error"]["code"] == "magic_word_required"

    bad = await fresh.post("/auth/unlock", json={"magic_word": "wrong"})
    assert bad.status_code == 401
    ok = await fresh.post("/auth/unlock", json={"magic_word": "  ýyldyrym "})
    assert ok.status_code == 200
    assert (await fresh.get("/thoughts")).status_code == 200

    await fresh.post("/auth/lock")
    assert (await fresh.get("/thoughts")).status_code == 423
    await fresh.aclose()


async def test_magic_word_is_not_a_login(
    anon: httpx.AsyncClient, client: httpx.AsyncClient
) -> None:
    await client.put("/auth/magic-word", json={"password": OWNER_PASSWORD, "magic_word": "secret1"})
    # Without a session the magic word is useless.
    assert (await anon.post("/auth/unlock", json={"magic_word": "secret1"})).status_code == 401


async def test_magic_word_unlock_rate_limited(client: httpx.AsyncClient) -> None:
    await client.put("/auth/magic-word", json={"password": OWNER_PASSWORD, "magic_word": "secret1"})
    fresh = await login(OWNER_EMAIL, OWNER_PASSWORD)
    codes = [
        (await fresh.post("/auth/unlock", json={"magic_word": "x"})).status_code for _ in range(6)
    ]
    assert codes[-1] == 429
    await fresh.aclose()


async def test_password_change_revokes_other_sessions(client: httpx.AsyncClient) -> None:
    other = await login(OWNER_EMAIL, OWNER_PASSWORD)
    resp = await client.post(
        "/auth/password",
        json={"current_password": OWNER_PASSWORD, "new_password": "a brand new passphrase"},
    )
    assert resp.status_code == 200
    assert (await other.get("/thoughts")).status_code == 401
    assert (await client.get("/thoughts")).status_code == 200
    await other.aclose()


async def test_service_token_scopes(client: httpx.AsyncClient, owner, db) -> None:  # type: ignore[no-untyped-def]
    from app.modules.pikirler.services.auth import create_service_client

    _, token = await create_service_client(
        db, owner.id, "jemshit", ["thoughts:write", "thoughts:read"], "jemshit"
    )
    async with _client() as svc:
        svc.headers["Authorization"] = f"Bearer {token}"
        created = await new_thought(svc, "Jemşit, ýadyňa ýaz: test")
        assert created["source"] == "jemshit"
        # No CSRF needed for bearer tokens, but no account-level access either.
        assert (await svc.get("/settings")).status_code == 403
        assert (await svc.get("/auth/sessions")).status_code == 403
        assert (await svc.post("/search/semantic", json={"query": "x"})).status_code in (403, 404)
        svc.headers["Authorization"] = "Bearer pks_invalid"
        assert (await svc.get("/thoughts")).status_code == 401


async def test_display_name_is_editable_in_settings(client: httpx.AsyncClient) -> None:
    resp = await client.patch("/settings", json={"display_name": "  Merdan  "})
    assert resp.status_code == 200 and resp.json()["display_name"] == "Merdan"
    assert (await client.get("/auth/me")).json()["owner"]["display_name"] == "Merdan"
    resp = await client.patch("/settings", json={"display_name": ""})
    assert resp.json()["display_name"] is None
