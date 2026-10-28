from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.conftest import new_thought


async def test_reply_creates_thread_and_orders_chronologically(client: httpx.AsyncClient) -> None:
    a = await new_thought(client, "I want to create my AI assistant.")
    b = (
        await client.post(
            f"/thoughts/{a['id']}/replies",
            json={"content": "Gemini could be its reasoning engine."},
        )
    ).json()
    c = (
        await client.post(
            f"/thoughts/{b['id']}/replies",
            json={"content": "I should connect it to my personal notes."},
        )
    ).json()
    assert b["thread_id"] == c["thread_id"] is not None
    assert b["parent_id"] == a["id"] and c["parent_id"] == b["id"]
    root = (await client.get(f"/thoughts/{a['id']}")).json()
    assert root["thread_id"] == b["thread_id"] and root["reply_count"] == 1

    timeline = (await client.get(f"/threads/{b['thread_id']}/timeline")).json()["items"]
    assert [i["thought"]["id"] for i in timeline] == [a["id"], b["id"], c["id"]]
    assert [i["depth"] for i in timeline] == [0, 1, 2]
    assert timeline[1]["previous_id"] == a["id"] and timeline[1]["next_id"] == c["id"]

    thread = (await client.get(f"/threads/{b['thread_id']}")).json()
    assert thread["root_thought_id"] == a["id"] and thread["thought_count"] == 3


async def test_branching_replies_depth_first(client: httpx.AsyncClient) -> None:
    root = await new_thought(client, "root")
    first = (await client.post(f"/thoughts/{root['id']}/replies", json={"content": "first"})).json()
    second = (
        await client.post(f"/thoughts/{root['id']}/replies", json={"content": "second"})
    ).json()
    nested = (
        await client.post(f"/thoughts/{first['id']}/replies", json={"content": "nested"})
    ).json()
    thread = (await client.get(f"/threads/{root['thread_id'] or first['thread_id']}")).json()
    assert [i["thought"]["id"] for i in thread["items"]] == [
        root["id"],
        first["id"],
        nested["id"],
        second["id"],
    ]
    timeline = (await client.get(f"/threads/{first['thread_id']}/timeline")).json()["items"]
    assert [i["thought"]["id"] for i in timeline] == [
        root["id"],
        first["id"],
        second["id"],
        nested["id"],
    ]


async def test_reply_to_missing_or_deleted_parent(client: httpx.AsyncClient) -> None:
    missing = await client.post(f"/thoughts/{uuid.uuid4()}/replies", json={"content": "x"})
    assert missing.status_code == 404
    t = await new_thought(client, "gone")
    await client.delete(f"/thoughts/{t['id']}")
    assert (
        await client.post(f"/thoughts/{t['id']}/replies", json={"content": "x"})
    ).status_code == 404


async def test_database_rejects_cycles(client: httpx.AsyncClient, db) -> None:  # type: ignore[no-untyped-def]
    a = await new_thought(client, "a")
    b = (await client.post(f"/thoughts/{a['id']}/replies", json={"content": "b"})).json()
    c = (await client.post(f"/thoughts/{b['id']}/replies", json={"content": "c"})).json()
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        await db.execute(
            text("UPDATE thoughts SET parent_id = :c WHERE id = :a"), {"c": c["id"], "a": a["id"]}
        )
    assert "cycle" in str(exc.value)
    await db.rollback()
    with pytest.raises((IntegrityError, DBAPIError)):
        await db.execute(text("UPDATE thoughts SET parent_id = id WHERE id = :a"), {"a": a["id"]})
    await db.rollback()


async def test_database_rejects_cross_owner_parent(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, db
) -> None:  # type: ignore[no-untyped-def]
    mine = await new_thought(client, "mine")
    theirs = await new_thought(other_client, "theirs")
    with pytest.raises((IntegrityError, DBAPIError)) as exc:
        await db.execute(
            text("UPDATE thoughts SET parent_id = :p WHERE id = :t"),
            {"p": mine["id"], "t": theirs["id"]},
        )
    assert "another owner" in str(exc.value)
    await db.rollback()


async def test_cannot_reply_to_someone_elses_thought(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    mine = await new_thought(client, "private idea")
    resp = await other_client.post(f"/thoughts/{mine['id']}/replies", json={"content": "hijack"})
    assert resp.status_code == 404
    thread_reply = (
        await client.post(f"/thoughts/{mine['id']}/replies", json={"content": "mine too"})
    ).json()
    assert (await other_client.get(f"/threads/{thread_reply['thread_id']}")).status_code == 404
