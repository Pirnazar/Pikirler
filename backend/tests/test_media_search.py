import sqlite3
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.pikirler.models import Thought
from app.modules.pikirler.models.media_search import MediaEntry, MediaPassage
from app.modules.pikirler.services import media_corpus
from tests.helpers import run_jobs
from tests.test_cinema import add, upload
from tests.test_cinema import movie_provider as movie_provider


@pytest.fixture
def media_archive(tmp_path, monkeypatch, owner):
    path = tmp_path / "youtube.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE youtube_videos(id INTEGER PRIMARY KEY,youtube_video_id TEXT,title TEXT,description TEXT,
          channel_title TEXT,artist TEXT,track_title TEXT,published_at TEXT,category_id TEXT,thumbnail_url TEXT,content_kind TEXT);
        CREATE TABLE youtube_transcripts(id INTEGER PRIMARY KEY,video_id INTEGER,language_code TEXT,full_text TEXT,status TEXT);
        CREATE TABLE song_lyrics(id INTEGER PRIMARY KEY,video_id INTEGER,language_code TEXT,full_text TEXT,status TEXT);
        CREATE TABLE youtube_transcript_segments(id INTEGER PRIMARY KEY,transcript_id INTEGER,segment_index INTEGER,start_seconds REAL,duration_seconds REAL,text TEXT);
        CREATE TABLE song_lyric_lines(id INTEGER PRIMARY KEY,lyrics_id INTEGER,line_index INTEGER,start_seconds REAL,text TEXT);
        INSERT INTO youtube_videos VALUES(1,'abcdefghijk','Наука','Экспедиция в космос','Канал',NULL,NULL,'2022-01-01','28',NULL,'video');
        INSERT INTO youtube_videos VALUES(2,'lmnopqrstuv','Song','Music video','Artist','Artist','Song','2020-01-01','10',NULL,'music');
        INSERT INTO youtube_transcripts VALUES(1,1,'ru','Космические корабли ищут новый дом.','ok');
        INSERT INTO youtube_transcript_segments VALUES(1,1,0,62.5,3.5,'Космические корабли ищут новый дом.');
        INSERT INTO song_lyrics VALUES(1,2,'en','We travel beyond the stars.','ok');
        INSERT INTO song_lyric_lines VALUES(1,1,0,15.0,'We travel beyond the stars.');
        """)
    monkeypatch.setenv("PIKIRLER_YOUTUBE_ARCHIVE", str(path))
    monkeypatch.setattr(get_settings(), "youtube_archive_owner_id", str(owner.id))
    monkeypatch.setattr(get_settings(), "media_encoder_url", "")
    return path


async def sync(client):
    assert (await client.post("/media-search/refresh")).status_code == 202
    assert await run_jobs(media_corpus.SYNC_JOB) == ["succeeded"]


async def test_archive_without_owner_binding_is_not_exposed(
    client, other_client, media_archive, monkeypatch
):
    monkeypatch.setattr(get_settings(), "youtube_archive_owner_id", "")
    for session in (client, other_client):
        listing = await session.get("/youtube-archive")
        assert listing.status_code == 200
        assert listing.json() == {"configured": False, "items": [], "total": 0}
        assert (await session.get("/youtube-archive/abcdefghijk")).status_code == 404
    await sync(client)
    assert (await client.get("/media-search/status")).json()["passages"] == 0


async def test_unified_filters_timestamps_pagination_and_owner(
    client, other_client, media_archive, movie_provider
):
    movie_id = await add(client)
    await upload(client, movie_id)
    await run_jobs("cinema_metadata")
    await sync(client)
    for body, title in [
        (
            {
                "query": "корабль",
                "kind": "video",
                "language": "ru",
                "year": 2022,
                "genre": "Наука и технологии",
            },
            "Наука",
        ),
        ({"query": "корабль", "kind": "movie", "language": "ru"}, "Интерстеллар"),
        ({"query": "stars", "kind": "lyrics", "language": "en", "year": 2020}, "Song"),
    ]:
        response = await client.post("/media-search", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1 and response.json()["items"][0]["title"] == title
    data = (await client.post("/media-search", json={"query": "корабль", "kind": "video"})).json()[
        "items"
    ][0]
    assert data["start_ms"] == 62500 and data["url"].endswith("&t=62")
    assert (
        await client.post("/media-search", json={"query": "корабль", "offset": 1, "limit": 1})
    ).json()["total"] == 2
    assert (await other_client.post("/media-search", json={"query": "корабль"})).json()[
        "total"
    ] == 0
    assert (await other_client.get("/media-search/facets")).json()["years"] == []
    assert (await other_client.get("/youtube-archive/abcdefghijk")).status_code == 404
    facets = (await client.get("/media-search/facets")).json()
    assert 2022 in facets["years"] and "ru" in facets["languages"]
    before = (await client.get("/media-search/status")).json()
    await sync(client)
    assert (await client.get("/media-search/status")).json()["passages"] == before["passages"]


async def test_semantic_retrieval_uses_vector_and_respects_filters(
    client, other_client, db, media_archive, monkeypatch
):
    await sync(client)
    passages = list((await db.execute(select(MediaPassage))).scalars())
    model = get_settings().media_encoder_model
    for p in passages:
        p.embedding = [1.0, *([0.0] * 383)] if p.part == "lyrics" else [0.0, 1.0, *([0.0] * 382)]
        p.embedding_model = model
    await db.commit()
    monkeypatch.setattr(media_corpus, "encode", AsyncMock(return_value=[[1.0, *([0.0] * 383)]]))
    response = await client.post(
        "/media-search",
        json={
            "query": "путешествие к далёким мирам",
            "mode": "semantic",
            "language": "en",
            "kind": "lyrics",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["title"] == "Song"
    assert (
        await other_client.post("/media-search", json={"query": "далёкие миры", "mode": "semantic"})
    ).json()["total"] == 0


async def test_citation_is_idempotent_owner_scoped_and_survives_source_update(
    client, other_client, db, media_archive
):
    await sync(client)
    found = (await client.post("/media-search", json={"query": "корабль", "kind": "video"})).json()[
        "items"
    ][0]
    body = {"source": "passage", "reference": found["id"], "position": 0}
    denied = await other_client.post("/media-search/quotes", json=body)
    assert denied.status_code == 404
    first = await client.post("/media-search/quotes", json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/media-search/quotes", json=body)
    assert (
        second.json()["thought_id"] == first.json()["thought_id"] and second.json()["already_saved"]
    )
    note = (await client.get("/thoughts/" + first.json()["thought_id"])).json()
    assert note["media_quote"]["language"] == "ru" and note["media_quote"]["start_ms"] == 62500
    assert "00:01:02" in note["content"] and note["media_quote"]["url"].endswith("&t=62")
    assert await db.scalar(select(func.count()).select_from(Thought)) == 1
    with sqlite3.connect(media_archive) as source:
        source.execute("UPDATE youtube_videos SET title='Наука: обновлено' WHERE id=1")
        source.execute(
            "UPDATE youtube_transcripts SET full_text='Новые планеты ждут исследователей.' WHERE id=1"
        )
        source.execute(
            "UPDATE youtube_transcript_segments SET text='Новые планеты ждут исследователей.' WHERE id=1"
        )
    await sync(client)
    original = (await client.get("/thoughts/" + first.json()["thought_id"])).json()
    assert original["media_quote"]["title"] == "Наука"
    assert original["media_quote"]["text"] == "Космические корабли ищут новый дом."
    assert (await client.post("/media-search", json={"query": "планеты", "kind": "video"})).json()[
        "total"
    ] == 1
    await client.delete("/thoughts/" + first.json()["thought_id"])
    replacement = await client.post("/media-search/quotes", json=body)
    assert replacement.json()["thought_id"] != first.json()["thought_id"]


async def test_movie_lists_rating_comment_and_conflicts(client, other_client, movie_provider):
    mid = await add(client)
    response = await client.patch(
        f"/movies/{mid}/preferences",
        json={
            "expected_version": 1,
            "watch_status": "want",
            "is_favorite": True,
            "user_rating": 9,
            "user_comment": "Пересмотреть всей семьёй",
        },
    )
    assert response.status_code == 200, response.text
    assert (await client.get("/movies", params={"watch_status": "want"})).json()["total"] == 1
    assert (await client.get("/movies", params={"watch_status": "watched"})).json()["total"] == 0
    assert (await client.get("/movies", params={"favorite": True})).json()["total"] == 1
    assert (
        await other_client.patch(
            f"/movies/{mid}/preferences", json={"expected_version": 2, "user_rating": 1}
        )
    ).status_code == 404
    assert (
        await client.patch(
            f"/movies/{mid}/preferences", json={"expected_version": 1, "user_rating": 1}
        )
    ).status_code == 409
    assert (
        await client.patch(
            f"/movies/{mid}/preferences", json={"expected_version": 2, "user_rating": 11}
        )
    ).status_code == 422
    await run_jobs("cinema_metadata")
    final = await client.patch(
        f"/movies/{mid}/preferences",
        json={"expected_version": 2, "watch_status": "watched", "user_rating": None},
    )
    assert final.json()["watched_at"] and final.json()["user_rating"] is None
    assert final.json()["user_comment"] == "Пересмотреть всей семьёй"


async def test_quote_direct_cinema_and_csrf(client, movie_provider):
    mid = await add(client)
    await upload(client, mid)
    cue = (await client.get(f"/movies/{mid}/cues")).json()["items"][0]
    response = await client.post(
        "/media-search/quotes", json={"source": "cinema", "reference": str(cue["id"])}
    )
    assert response.status_code == 201, response.text
    note = (await client.get("/thoughts/" + response.json()["thought_id"])).json()
    assert note["media_quote"]["movie_id"] == mid and note["media_quote"]["start_ms"] == 1250
    focused = await client.get(
        f"/movies/{mid}/cues",
        params={"subtitle_id": cue["subtitle_id"], "around_ms": 3723000, "limit": 1},
    )
    assert focused.status_code == 200 and focused.json()["offset"] == 1
    assert focused.json()["items"][0]["start_ms"] == 3723000
    client.headers.pop("X-CSRF-Token")
    assert (
        await client.post(
            "/media-search/quotes", json={"source": "cinema", "reference": str(cue["id"])}
        )
    ).status_code == 403


async def test_title_updates_invalidate_only_affected_vectors(client, db, media_archive):
    await sync(client)
    passages = list((await db.execute(select(MediaPassage))).scalars())
    for passage in passages:
        passage.embedding = [1.0, *([0.0] * 383)]
        passage.embedding_model = get_settings().media_encoder_model
    await db.commit()
    with sqlite3.connect(media_archive) as source:
        source.execute("UPDATE youtube_videos SET title='Путешествие в космос' WHERE id=1")
    await sync(client)
    rows = (
        await db.execute(
            select(MediaPassage, MediaEntry)
            .join(MediaEntry, MediaPassage.entry_id == MediaEntry.id)
            .execution_options(populate_existing=True)
        )
    ).all()
    changed = [p for p, e in rows if e.source_key == "youtube:abcdefghijk"]
    unchanged = [p for p, e in rows if e.source_key == "youtube:lmnopqrstuv"]
    assert changed and all(p.embedding is None for p in changed)
    assert unchanged and all(p.embedding is not None for p in unchanged)
