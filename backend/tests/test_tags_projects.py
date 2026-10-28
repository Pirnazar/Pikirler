from __future__ import annotations

import httpx

from tests.conftest import new_thought


async def test_tag_autocomplete_and_popularity(client: httpx.AsyncClient) -> None:
    await new_thought(client, "one", tags=["Ýyldyrym Market", "ai"])
    await new_thought(client, "two", tags=["ýyldyrym cargo", "AI"])
    await new_thought(client, "three", tags=["Zehin"])
    complete = (await client.get("/tags", params={"q": "ÝYL"})).json()
    assert {t["name"] for t in complete} == {"Ýyldyrym Market", "ýyldyrym cargo"}
    popular = (await client.get("/tags", params={"sort": "popular"})).json()
    assert popular[0]["normalized_name"] == "ai" and popular[0]["usage_count"] == 2
    # Same tag regardless of case: one row.
    assert sum(1 for t in popular if t["normalized_name"] == "ai") == 1


async def test_rename_and_merge_tags(client: httpx.AsyncClient) -> None:
    a = await new_thought(client, "a", tags=["secondbrain"])
    b = await new_thought(client, "b", tags=["second-brain", "secondbrain"])
    tags = {t["normalized_name"]: t for t in (await client.get("/tags")).json()}
    dup, keep = tags["second-brain"], tags["secondbrain"]

    clash = await client.patch(f"/tags/{dup['id']}", json={"name": "SecondBrain"})
    assert clash.status_code == 409

    merged = await client.post(f"/tags/{dup['id']}/merge", json={"into_tag_id": keep["id"]})
    assert merged.status_code == 200 and merged.json()["usage_count"] == 2
    for t in (a, b):
        body = (await client.get(f"/thoughts/{t['id']}")).json()
        assert body["tags"] == ["secondbrain"]

    renamed = await client.patch(f"/tags/{keep['id']}", json={"name": "Second Brain"})
    assert renamed.json()["name"] == "Second Brain"
    assert (await client.get("/thoughts", params={"tag": "second brain"})).json()["items"]


async def test_projects_crud_and_links(client: httpx.AsyncClient) -> None:
    names = ["Second Brain", "Jemşit", "Ýyldyrym Cargo", "Ýyldyrym Market", "Zehin"]
    projects = [(await client.post("/projects", json={"name": n})).json() for n in names]
    assert (await client.post("/projects", json={"name": "zehin"})).status_code == 409
    t = await new_thought(
        client, "multi-project", project_ids=[projects[0]["id"], projects[1]["id"]]
    )
    assert {p["name"] for p in t["projects"]} == {"Second Brain", "Jemşit"}
    p0 = (await client.get(f"/projects/{projects[0]['id']}")).json()
    assert p0["thought_count"] == 1
    upd = await client.patch(
        f"/projects/{projects[0]['id']}", json={"status": "paused", "external_ref": "sb:project:1"}
    )
    assert upd.json()["status"] == "paused" and upd.json()["external_ref"] == "sb:project:1"
    assert len((await client.get("/timeline/projects")).json()["items"]) == 1
    assert (await client.delete(f"/projects/{projects[0]['id']}")).status_code == 204
    after = (await client.get(f"/thoughts/{t['id']}")).json()
    assert [p["name"] for p in after["projects"]] == ["Jemşit"]


async def test_relations_user_and_ai(client: httpx.AsyncClient, owner, db) -> None:  # type: ignore[no-untyped-def]
    from app.modules.pikirler.services import relations

    a = await new_thought(client, "a")
    b = await new_thought(client, "b")
    c = await new_thought(client, "c")
    made = await client.post(
        f"/thoughts/{a['id']}/relations",
        json={"target_thought_id": b["id"], "relation_type": "supports"},
    )
    assert made.status_code == 201
    assert made.json()["status"] == "confirmed" and made.json()["ai_generated"] is False
    assert (
        await client.post(f"/thoughts/{a['id']}/relations", json={"target_thought_id": a["id"]})
    ).status_code == 422

    import uuid as _uuid

    await relations.suggest(
        db,
        owner.id,
        _uuid.UUID(a["id"]),
        _uuid.UUID(c["id"]),
        "contradicts",
        0.81,
        "opposite claims",
    )
    await db.commit()
    rels = (await client.get(f"/thoughts/{a['id']}/relations")).json()
    suggested = next(r for r in rels if r["created_by"] == "ai")
    assert suggested["status"] == "suggested" and suggested["ai_generated"] is True
    confirmed = await client.post(f"/relations/{suggested['id']}/confirm")
    assert confirmed.json()["status"] == "confirmed" and confirmed.json()["confirmed_at"]
