from __future__ import annotations

import asyncio

import httpx

from tests.conftest import new_thought


async def test_create_matches_contract(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/thoughts",
        json={
            "content": "I want to create my own personal AI assistant.",
            "thought_type": "idea",
            "status": "published",
            "tags": ["secondbrain", "ai"],
            "project_ids": [],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    for key in (
        "id",
        "content",
        "thought_type",
        "status",
        "created_at",
        "updated_at",
        "tags",
        "attachments",
        "thread_id",
        "is_bookmarked",
        "is_pinned",
    ):
        assert key in body
    assert sorted(body["tags"]) == ["ai", "secondbrain"]
    assert body["visibility"] == "private" and body["version"] == 1
    assert body["thread_id"] is None and body["attachments"] == []


async def test_long_text_has_no_twitter_limit(client: httpx.AsyncClient) -> None:
    long = "söz " * 20_000
    body = await new_thought(client, long)
    assert len(body["content"]) == len(long)


async def test_published_needs_content_but_draft_does_not(client: httpx.AsyncClient) -> None:
    resp = await client.post("/thoughts", json={"content": "   ", "status": "published"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "empty_thought"
    draft = await new_thought(client, "", status="draft")
    assert draft["status"] == "draft"
    # Publishing the empty draft is refused too.
    resp = await client.patch(
        f"/thoughts/{draft['id']}", json={"expected_version": 1, "status": "published"}
    )
    assert resp.status_code == 422


async def test_hashtags_become_tags_with_original_spelling(client: httpx.AsyncClient) -> None:
    body = await new_thought(client, "Bu gün #Ýyldyrym_Cargo we #ИИ barada pikir")
    assert sorted(body["tags"]) == sorted(["Ýyldyrym_Cargo", "ИИ"])
    norm = {t["normalized_name"] for t in body["tag_refs"]}
    assert norm == {"ýyldyrym_cargo", "ии"}


async def test_edit_creates_revisions_and_restore(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "first")
    r1 = await client.patch(
        f"/thoughts/{t['id']}", json={"expected_version": 1, "content": "second"}
    )
    assert r1.status_code == 200 and r1.json()["version"] == 2
    r2 = await client.patch(
        f"/thoughts/{t['id']}", json={"expected_version": 2, "content": "third"}
    )
    assert r2.json()["content"] == "third"

    revisions = (await client.get(f"/thoughts/{t['id']}/revisions")).json()
    assert [r["revision_number"] for r in revisions] == [3, 2, 1]
    assert revisions[0]["previous_content"] == "second" and revisions[0]["new_content"] == "third"

    first = next(r for r in revisions if r["revision_number"] == 1)
    restored = await client.post(f"/thoughts/{t['id']}/revisions/{first['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["content"] == "first" and restored.json()["version"] == 4
    revisions = (await client.get(f"/thoughts/{t['id']}/revisions")).json()
    assert revisions[0]["change_source"] == "restore" and revisions[0]["revision_number"] == 4


async def test_stale_version_is_rejected(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "base")
    ok = await client.patch(f"/thoughts/{t['id']}", json={"expected_version": 1, "content": "a"})
    assert ok.status_code == 200
    stale = await client.patch(f"/thoughts/{t['id']}", json={"expected_version": 1, "content": "b"})
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current_version"] == 2


async def test_concurrent_updates_only_one_wins(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "base")
    results = await asyncio.gather(
        *[
            client.patch(
                f"/thoughts/{t['id']}", json={"expected_version": 1, "content": f"edit {i}"}
            )
            for i in range(5)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409, 409, 409, 409]
    final = (await client.get(f"/thoughts/{t['id']}")).json()
    assert final["version"] == 2
    revisions = (await client.get(f"/thoughts/{t['id']}/revisions")).json()
    assert len(revisions) == 2


async def test_soft_delete_and_restore(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "to be deleted")
    assert (await client.delete(f"/thoughts/{t['id']}")).status_code == 204
    assert (await client.get(f"/thoughts/{t['id']}")).status_code == 404
    feed = (await client.get("/thoughts")).json()["items"]
    assert t["id"] not in [x["id"] for x in feed]
    trash = (await client.get("/thoughts", params={"deleted": True})).json()["items"]
    assert [x["id"] for x in trash] == [t["id"]]
    restored = await client.post(f"/thoughts/{t['id']}/restore")
    assert restored.status_code == 200 and restored.json()["deleted_at"] is None
    assert (await client.post(f"/thoughts/{t['id']}/restore")).status_code == 409


async def test_bookmark_and_pin(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "keep this")
    assert (await client.post(f"/thoughts/{t['id']}/bookmark")).json()["is_bookmarked"] is True
    assert (await client.post(f"/thoughts/{t['id']}/pin")).json()["is_pinned"] is True
    bookmarked = (await client.get("/timeline/bookmarked")).json()["items"]
    assert [x["id"] for x in bookmarked] == [t["id"]]
    home = (await client.get("/timeline")).json()
    assert [x["id"] for x in home["pinned"]] == [t["id"]]
    assert (await client.delete(f"/thoughts/{t['id']}/bookmark")).json()["is_bookmarked"] is False
    assert (await client.get("/timeline/bookmarked")).json()["items"] == []


async def test_drafts_are_not_in_timeline(client: httpx.AsyncClient) -> None:
    await new_thought(client, "draft text", status="draft")
    pub = await new_thought(client, "public text")
    timeline = (await client.get("/timeline")).json()["items"]
    assert [x["id"] for x in timeline] == [pub["id"]]
    drafts = (await client.get("/thoughts", params={"status": "draft"})).json()["items"]
    assert len(drafts) == 1


async def test_cursor_pagination_is_stable(client: httpx.AsyncClient) -> None:
    created = [await new_thought(client, f"thought {i}") for i in range(7)]
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = (await client.get("/thoughts", params=params)).json()
        seen += [x["id"] for x in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [t["id"] for t in reversed(created)]
    assert (await client.get("/thoughts", params={"cursor": "garbage"})).status_code == 422


async def test_filters(client: httpx.AsyncClient) -> None:
    project = (await client.post("/projects", json={"name": "Jemşit"})).json()
    a = await new_thought(
        client, "idea one", thought_type="idea", tags=["AI"], project_ids=[project["id"]]
    )
    await new_thought(client, "journal entry", thought_type="journal")
    by_type = (await client.get("/thoughts", params={"type": "idea"})).json()["items"]
    assert [x["id"] for x in by_type] == [a["id"]]
    by_tag = (await client.get("/thoughts", params={"tag": "ai"})).json()["items"]
    assert [x["id"] for x in by_tag] == [a["id"]]
    by_project = (await client.get("/thoughts", params={"project_id": project["id"]})).json()
    assert [x["id"] for x in by_project["items"]] == [a["id"]]
    future = (await client.get("/thoughts", params={"date_from": "2100-01-01T00:00:00Z"})).json()
    assert future["items"] == []


async def test_occurred_at_is_separate_from_created_at(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "remembering 2019", occurred_at="2019-05-01T10:00:00Z")
    assert t["occurred_at"].startswith("2019-05-01")
    assert t["created_at"][:4] != "2019"
