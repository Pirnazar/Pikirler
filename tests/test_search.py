from __future__ import annotations

import httpx
import pytest

from app.modules.pikirler.integrations import embeddings
from tests.conftest import new_thought
from tests.fakes import FakeEmbeddings
from tests.helpers import run_jobs


async def _ids(client: httpx.AsyncClient, q: str, **params: object) -> list[str]:
    resp = await client.get("/search", params={"q": q, **params})
    assert resp.status_code == 200, resp.text
    return [r["thought_id"] for r in resp.json()["results"]]


async def test_russian_morphology(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "Мне пришла в голову интересная мысль о памяти")
    await new_thought(client, "Совсем другое")
    assert await _ids(client, "мысли") == [t["id"]]
    assert await _ids(client, "памятью") == [t["id"]]


async def test_english_stemming_and_phrase(client: httpx.AsyncClient) -> None:
    a = await new_thought(client, "I keep running into the same personal assistant idea")
    b = await new_thought(client, "assistant personal is backwards here")
    assert set(await _ids(client, "run")) == {a["id"]}
    assert await _ids(client, '"personal assistant"') == [a["id"]]
    assert set(await _ids(client, "personal assistant")) == {a["id"], b["id"]}
    assert await _ids(client, "assistant -backwards") == [a["id"]]


async def test_turkmen_with_and_without_diacritics(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "Ýyldyrym Cargo üçin täze pikir: ýük daşamak")
    assert await _ids(client, "ýyldyrym") == [t["id"]]
    assert await _ids(client, "yyldyrym") == [t["id"]]
    assert await _ids(client, "ÝYLDYRYM") == [t["id"]]
    assert await _ids(client, "tize") == []
    assert await _ids(client, "täze") == [t["id"]]
    assert await _ids(client, "taze") == [t["id"]]


async def test_chinese_without_spaces(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "我想建立自己的人工智能助手")
    await new_thought(client, "今天天气很好")
    assert await _ids(client, "人工智能") == [t["id"]]
    assert await _ids(client, "智能") == [t["id"]]
    assert await _ids(client, "助手") == [t["id"]]
    assert await _ids(client, "天气") != [t["id"]]


async def test_unicode_normalization(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "ＡＩ assistant ﬁle")  # full-width letters, ligature
    assert await _ids(client, "ai") == [t["id"]]
    assert await _ids(client, "file") == [t["id"]]


async def test_mixed_language_thought(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "Bu gün Second Brain барада подумал, 人工智能 hem gerek")
    for q in ("second brain", "подумать", "人工智能", "gün"):
        assert await _ids(client, q) == [t["id"]], q


async def test_fuzzy_fallback_and_no_results(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "Jemşit should become my voice assistant")
    resp = await client.get("/search", params={"q": "asistant"})
    results = resp.json()["results"]
    assert [r["thought_id"] for r in results] == [t["id"]]
    assert results[0]["match_type"] == "fuzzy"
    assert await _ids(client, "квантовая хромодинамика") == []


async def test_filters_and_result_shape(client: httpx.AsyncClient) -> None:
    project = (await client.post("/projects", json={"name": "Zehin"})).json()
    a = await new_thought(
        client,
        "memory palace idea",
        thought_type="idea",
        tags=["memory"],
        project_ids=[project["id"]],
    )
    b = await new_thought(client, "memory journal", thought_type="journal")
    assert await _ids(client, "memory", type="idea") == [a["id"]]
    assert await _ids(client, "memory", tag="memory") == [a["id"]]
    assert await _ids(client, "memory", project_id=project["id"]) == [a["id"]]
    reply = (
        await client.post(f"/thoughts/{b['id']}/replies", json={"content": "more memory notes"})
    ).json()
    assert await _ids(client, "memory", thread_id=reply["thread_id"]) != []
    result = (await client.get("/search", params={"q": "palace"})).json()["results"][0]
    assert {"thought_id", "snippet", "created_at", "tags", "match_type", "score", "url"} <= set(
        result
    )
    assert result["url"].endswith(a["id"]) and result["tags"] == ["memory"]


async def test_deleted_and_foreign_thoughts_not_found(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    t = await new_thought(client, "unique zebra thought")
    assert await _ids(other_client, "zebra") == []
    await client.delete(f"/thoughts/{t['id']}")
    assert await _ids(client, "zebra") == []


async def test_hybrid_without_embeddings_still_works(client: httpx.AsyncClient) -> None:
    t = await new_thought(client, "keyword only search works")
    resp = (await client.post("/search/hybrid", json={"query": "keyword"})).json()
    assert resp["semantic"] == "disabled"
    assert [r["thought_id"] for r in resp["results"]] == [t["id"]]
    semantic = await client.post("/search/semantic", json={"query": "keyword"})
    assert semantic.status_code == 503


async def test_semantic_and_hybrid_with_embeddings(client: httpx.AsyncClient) -> None:
    fake = FakeEmbeddings()
    embeddings.set_embedding_provider(fake)
    a = await new_thought(client, "build a personal ai assistant with gemini")
    b = await new_thought(client, "groceries: milk bread eggs")
    statuses = await run_jobs("embed_thought")
    assert statuses == ["succeeded", "succeeded"]
    sem = (await client.post("/search/semantic", json={"query": "assistant gemini"})).json()
    assert sem["results"][0]["thought_id"] == a["id"]
    hyb = (await client.post("/search/hybrid", json={"query": "assistant"})).json()
    assert hyb["semantic"] == "ok"
    assert hyb["results"][0]["thought_id"] == a["id"]
    assert hyb["results"][0]["match_type"] == "hybrid"
    similar = (await client.get(f"/thoughts/{b['id']}/similar")).json()
    assert similar["semantic"] == "ok" and b["id"] not in [
        r["thought_id"] for r in similar["results"]
    ]
    # Re-indexing unchanged content does not call the provider again.
    calls = len(fake.calls)
    await client.post(f"/thoughts/{a['id']}/bookmark")
    await run_jobs("embed_thought")
    assert len(fake.calls) == calls


async def test_hybrid_survives_provider_outage(client: httpx.AsyncClient) -> None:
    embeddings.set_embedding_provider(FakeEmbeddings(fail=True))
    t = await new_thought(client, "outage resilient thought")
    resp = (await client.post("/search/hybrid", json={"query": "outage"})).json()
    assert resp["semantic"] == "unavailable"
    assert [r["thought_id"] for r in resp["results"]] == [t["id"]]


async def test_external_embeddings_need_consent(client: httpx.AsyncClient) -> None:
    fake = FakeEmbeddings(is_external=True)
    embeddings.set_embedding_provider(fake)
    await new_thought(client, "private thought for external provider")
    assert await run_jobs("embed_thought") == ["succeeded"]
    assert fake.calls == []  # skipped: nothing was sent
    resp = (await client.post("/search/hybrid", json={"query": "private"})).json()
    assert resp["semantic"] == "not_permitted"
    await client.patch("/settings", json={"ai_external_enabled": True})
    resp = (await client.post("/search/hybrid", json={"query": "private"})).json()
    assert resp["semantic"] == "ok"


@pytest.mark.parametrize("dims", [64])
async def test_vectors_of_other_models_are_ignored(
    client: httpx.AsyncClient, db, dims: int
) -> None:  # type: ignore[no-untyped-def]
    fake = FakeEmbeddings()
    embeddings.set_embedding_provider(fake)
    await new_thought(client, "model one vector")
    await run_jobs("embed_thought")
    other = FakeEmbeddings()
    other.model = "another-model"
    embeddings.set_embedding_provider(other)
    sem = (await client.post("/search/semantic", json={"query": "vector"})).json()
    assert sem["results"] == []  # never compares vectors across models
