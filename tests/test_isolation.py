"""IDOR: one owner must never read or change another owner's data."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.conftest import new_thought


async def test_thought_endpoints_hide_other_owners_data(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    t = await new_thought(client, "my secret", tags=["private"])
    tid = t["id"]
    attempts = [
        ("GET", f"/thoughts/{tid}", None),
        ("PATCH", f"/thoughts/{tid}", {"expected_version": 1, "content": "pwned"}),
        ("DELETE", f"/thoughts/{tid}", None),
        ("POST", f"/thoughts/{tid}/restore", None),
        ("POST", f"/thoughts/{tid}/bookmark", None),
        ("POST", f"/thoughts/{tid}/pin", None),
        ("GET", f"/thoughts/{tid}/revisions", None),
        ("GET", f"/thoughts/{tid}/relations", None),
    ]
    for method, path, body in attempts:
        resp = await other_client.request(method, path, json=body)
        assert resp.status_code == 404, (method, path, resp.status_code)

    other_feed = (await other_client.get("/thoughts")).json()["items"]
    assert other_feed == []
    assert (await other_client.get("/tags")).json() == []
    assert (await other_client.get("/thoughts", params={"tag": "private"})).json()["items"] == []
    # Untouched for the owner.
    mine = (await client.get(f"/thoughts/{tid}")).json()
    assert mine["content"] == "my secret" and mine["version"] == 1


async def test_cannot_link_foreign_objects(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    project = (await client.post("/projects", json={"name": "Zehin"})).json()
    mine = await new_thought(client, "mine")
    resp = await other_client.post(
        "/thoughts", json={"content": "x", "project_ids": [project["id"]]}
    )
    assert resp.status_code == 404
    theirs = await new_thought(other_client, "theirs")
    rel = await other_client.post(
        f"/thoughts/{theirs['id']}/relations", json={"target_thought_id": mine["id"]}
    )
    assert rel.status_code == 404
    assert (await other_client.get(f"/projects/{project['id']}")).status_code == 404
    tags = (await client.post("/tags", json={"name": "mine-only"})).json()
    assert (await other_client.patch(f"/tags/{tags['id']}", json={"name": "x"})).status_code == 404


async def test_database_enforces_same_owner_links(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, db
) -> None:  # type: ignore[no-untyped-def]
    mine = await new_thought(client, "mine", tags=["t"])
    theirs = await new_thought(other_client, "theirs")
    tag_id = (await client.get("/tags")).json()[0]["id"]
    with pytest.raises(IntegrityError):
        await db.execute(
            text(
                "INSERT INTO thought_tags (thought_id, tag_id, owner_id) "
                "SELECT :t, :tag, owner_id FROM thoughts WHERE id = :t"
            ),
            {"t": theirs["id"], "tag": tag_id},
        )
    await db.rollback()
    assert mine["id"]
