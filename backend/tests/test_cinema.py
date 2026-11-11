from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import ProviderUnavailable, ValidationFailed
from app.modules.pikirler.integrations import cinema as provider
from app.modules.pikirler.models import Job
from tests.helpers import run_jobs

SRT = """1
00:00:01,250 --> 00:00:04,500
<i>Космические корабли</i> возвращаются домой.

2
01:02:03,000 --> 01:02:05,000
The explorers were running home.
""".encode()


@pytest.fixture
def movie_provider(monkeypatch):
    metadata = {
        "imdb_id": "tt0816692",
        "title": "Interstellar",
        "year": "2014",
        "poster": "https://images.metahub.space/poster/small/tt0816692/img",
        "description": "A film about space.",
        "runtime": "169 min",
        "rating": "8.7",
        "genres": ["Sci-Fi"],
        "cast": ["Matthew McConaughey"],
        "director": ["Christopher Nolan"],
    }
    monkeypatch.setattr(provider, "get_movie", AsyncMock(return_value=metadata))
    monkeypatch.setattr(provider, "search_movies", AsyncMock(return_value=[metadata]))
    monkeypatch.setattr(provider, "subtitle_options", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        provider,
        "get_russian_movie",
        AsyncMock(
            return_value={
                **metadata,
                "title": "Интерстеллар",
                "description": "Экспедиция ищет новый дом среди звёзд.",
                "director": ["Кристофер Нолан"],
                "cast": ["Мэттью Макконахи"],
                "genres": ["фантастика"],
                "source": "tmdb",
                "source_url": "https://www.themoviedb.org/movie/157336",
            }
        ),
    )
    return metadata


async def add(client):
    response = await client.post("/movies", json={"imdb_id": "tt0816692", "query": "Интерстеллар"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def upload(client, movie_id, data=SRT):
    return await client.post(
        f"/movies/{movie_id}/subtitles/upload",
        data={"language": "ru"},
        files={"file": ("dialogue.srt", data, "text/plain")},
    )


async def test_add_is_idempotent_and_alias_search(client, db, movie_provider):
    movie_id = await add(client)
    assert await add(client) == movie_id
    provider.get_movie.assert_awaited_once()
    listing = (await client.get("/movies", params={"q": "Интерстеллар", "limit": 1})).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == movie_id
    assert (await client.get("/movies", params={"offset": 1})).json()["items"] == []
    jobs = (await db.execute(select(Job).where(Job.kind == "cinema_subtitles"))).scalars().all()
    assert len(jobs) == 1


async def test_upload_timestamp_search_morphology_and_duplicates(client, movie_provider):
    movie_id = await add(client)
    saved = await upload(client, movie_id)
    assert saved.status_code == 201, saved.text
    assert saved.json()["cue_count"] == 2
    assert (await upload(client, movie_id)).json()["id"] == saved.json()["id"]
    detail = (await client.get(f"/movies/{movie_id}")).json()
    assert detail["movie"]["subtitle_languages"]["ru"] == "ready"
    assert len(detail["tracks"]) == 1
    for query in ("корабль", "run"):
        result = await client.get("/movies/quotes", params={"q": query})
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 1, result.text
    cues = (await client.get(f"/movies/{movie_id}/cues", params={"limit": 1})).json()
    assert cues["total"] == 2
    assert cues["items"][0]["start_ms"] == 1250
    assert "<i>" not in cues["items"][0]["text"]
    assert (await client.get(f"/movies/{movie_id}/cues", params={"offset": 1})).json()["items"][0][
        "start_ms"
    ] == 3723000
    assert await run_jobs("cinema_subtitles") == ["succeeded"]
    provider.subtitle_options.assert_awaited_once_with("tt0816692", "en")
    assert (await client.get(f"/movies/{movie_id}")).json()["movie"]["subtitle_status"] == "partial"


async def test_owner_isolation_and_auth(client, other_client, anon, movie_provider):
    movie_id = await add(client)
    assert (await upload(client, movie_id)).status_code == 201
    assert (await other_client.get("/movies")).json()["total"] == 0
    assert (await other_client.get("/movies/quotes", params={"q": "корабль"})).json()["total"] == 0
    for path in (
        f"/movies/{movie_id}",
        f"/movies/{movie_id}/cues",
        f"/movies/{movie_id}/subtitles/options",
    ):
        assert (await other_client.get(path)).status_code == 404
    assert (await upload(other_client, movie_id)).status_code == 404
    assert (await other_client.post(f"/movies/{movie_id}/subtitles/auto")).status_code == 404
    assert (await other_client.post(f"/movies/{movie_id}/refresh")).status_code == 404
    assert (await anon.get("/movies")).status_code == 401
    client.headers.pop("X-CSRF-Token")
    assert (await client.post("/movies", json={"imdb_id": "tt0816692"})).status_code == 403


async def test_auto_subtitles_saved_and_missing_status(client, monkeypatch, movie_provider):
    movie_id = await add(client)
    choice = {
        "id": "1234",
        "language": "ru",
        "label": "Russian",
        "url": "https://subs5.strem.io/test",
    }

    async def choices(imdb_id, language):
        return [{**choice, "id": language + "1234", "language": language}]

    monkeypatch.setattr(provider, "subtitle_options", AsyncMock(side_effect=choices))
    monkeypatch.setattr(provider, "download_subtitle", AsyncMock(return_value=SRT))
    assert await run_jobs("cinema_subtitles") == ["succeeded"]
    result = (await client.get(f"/movies/{movie_id}")).json()
    assert result["movie"]["subtitle_status"] == "ready"
    assert result["movie"]["subtitle_languages"] == {"ru": "ready", "en": "ready"}
    assert [track["language"] for track in result["tracks"]] == ["ru", "en"]
    assert result["tracks"][0]["source"] == "opensubtitles"


async def test_russian_metadata_keeps_english_and_searches_both(client, movie_provider):
    movie_id = await add(client)
    assert await run_jobs("cinema_metadata") == ["succeeded"]
    data = (await client.get(f"/movies/{movie_id}")).json()["movie"]
    assert data["title"] == data["localized"]["en"]["title"] == "Interstellar"
    assert data["description"] == "A film about space."
    assert data["localized"]["ru"]["title"] == "Интерстеллар"
    assert data["metadata_status"] == "ready"
    for query in ("Интерстеллар", "Interstellar", "Экспедиция", "space", "Нолан"):
        assert (await client.get("/movies", params={"q": query})).json()["total"] == 1
    assert (await client.post(f"/movies/{movie_id}/refresh")).status_code == 202
    assert await run_jobs("cinema_metadata") == ["succeeded"]
    assert (await client.get(f"/movies/{movie_id}")).json()["movie"]["localized"]["en"][
        "description"
    ] == "A film about space."


async def test_english_track_does_not_block_russian_completion(client, monkeypatch, movie_provider):
    movie_id = await add(client)
    english = b"1\n00:00:01,000 --> 00:00:03,000\nExplorers are running home.\n"
    initial = await client.post(
        f"/movies/{movie_id}/subtitles/upload",
        data={"language": "en"},
        files={"file": ("en.srt", english, "text/plain")},
    )
    assert initial.status_code == 201
    ru = [{"id": "ru123", "label": "RU", "url": "https://subs5.strem.io/ru"}]
    monkeypatch.setattr(provider, "subtitle_options", AsyncMock(return_value=ru))
    monkeypatch.setattr(provider, "download_subtitle", AsyncMock(return_value=SRT))
    assert await run_jobs("cinema_subtitles") == ["succeeded"]
    provider.subtitle_options.assert_awaited_once_with("tt0816692", "ru")
    data = (await client.get(f"/movies/{movie_id}")).json()
    assert len(data["tracks"]) == 2 and data["tracks"][1]["id"] == initial.json()["id"]
    ru_hits = (await client.get("/movies/quotes", params={"q": "корабль", "language": "ru"})).json()
    assert ru_hits["total"] == 1 and ru_hits["items"][0]["language"] == "ru"
    assert (await client.get("/movies/quotes", params={"q": "корабль", "language": "en"})).json()[
        "total"
    ] == 0
    assert (await client.get("/movies/quotes", params={"q": "run", "language": "en"})).json()[
        "total"
    ] == 1
    provider.download_subtitle.reset_mock()
    assert (await client.post(f"/movies/{movie_id}/subtitles/auto")).status_code == 202
    assert await run_jobs("cinema_subtitles") == ["succeeded"]
    provider.download_subtitle.assert_not_awaited()


async def test_one_failed_language_keeps_other_track_and_retries_only_missing(
    client, db, monkeypatch, movie_provider
):
    movie_id = await add(client)

    async def choices(imdb_id, language):
        if language == "ru":
            raise ProviderUnavailable("Unavailable")
        return [{"id": "en123", "label": "EN", "url": "https://subs5.strem.io/en"}]

    monkeypatch.setattr(provider, "subtitle_options", AsyncMock(side_effect=choices))
    monkeypatch.setattr(provider, "download_subtitle", AsyncMock(return_value=SRT))
    assert await run_jobs("cinema_subtitles", rounds=1) == ["failed"]
    data = (await client.get(f"/movies/{movie_id}")).json()
    assert len(data["tracks"]) == 1 and data["tracks"][0]["language"] == "en"
    assert data["movie"]["subtitle_languages"] == {"ru": "failed", "en": "ready"}
    from app.core.database import utcnow

    job = (await db.execute(select(Job).where(Job.kind == "cinema_subtitles"))).scalar_one()
    job.run_after = utcnow()
    await db.commit()
    choices_mock = AsyncMock(
        return_value=[{"id": "ru123", "label": "RU", "url": "https://subs5.strem.io/ru"}]
    )
    monkeypatch.setattr(provider, "subtitle_options", choices_mock)
    assert await run_jobs("cinema_subtitles", rounds=1) == ["succeeded"]
    choices_mock.assert_awaited_once_with("tt0816692", "ru")
    assert (await client.get(f"/movies/{movie_id}")).json()["movie"]["subtitle_status"] == "ready"


async def test_rest_catalog_verifies_movie_language_and_single_disc(monkeypatch):
    item = {
        "IDSubtitle": "7716261",
        "IDMovieImdb": "816692",
        "SubLanguageID": "rus",
        "SubSumCD": "1",
        "SubDownloadsCnt": "20",
        "SubFileName": "film.rus.srt",
        "SubDownloadLink": "https://dl.opensubtitles.org/file.gz",
    }
    monkeypatch.setattr(
        provider,
        "_provider_data",
        AsyncMock(
            return_value=[
                item,
                {**item, "IDMovieImdb": "1234567"},
                {**item, "SubLanguageID": "eng"},
                {**item, "SubSumCD": "2"},
                {**item, "SubDownloadLink": "https://evil.invalid/x"},
            ]
        ),
    )
    result = await provider.subtitle_options("tt0816692", "ru")
    assert len(result) == 1 and result[0]["id"] == "7716261"
    provider._provider_data.assert_awaited_once_with(
        "https://rest.opensubtitles.org/search/imdbid-0816692/sublanguageid-rus"
    )


async def test_russian_metadata_validates_imdb_and_native_credits(monkeypatch):
    monkeypatch.setattr(
        provider,
        "provider_json",
        AsyncMock(
            return_value={
                "meta": {
                    "id": "tmdb:157336",
                    "imdb_id": "tt0816692",
                    "name": "Интерстеллар",
                    "runtime": "2h49min",
                    "links": [{"category": "Cast", "name": "Мэттью Макконахи"}],
                }
            }
        ),
    )
    result = await provider.get_russian_movie("tt0816692")
    assert result["cast"] == ["Мэттью Макконахи"]
    assert result["runtime"] == "2 ч 49 мин"
    with pytest.raises(ValidationFailed):
        await provider.get_russian_movie("tt1234567")


async def test_auto_missing_and_retry(client, movie_provider):
    movie_id = await add(client)
    assert await run_jobs("cinema_subtitles") == ["succeeded"]
    assert (await client.get(f"/movies/{movie_id}")).json()["movie"][
        "subtitle_status"
    ] == "not_found"
    assert (await client.post(f"/movies/{movie_id}/subtitles/auto")).status_code == 202


async def test_provider_failure_has_retry_and_final_status(client, db, monkeypatch, movie_provider):
    movie_id = await add(client)
    monkeypatch.setattr(
        provider, "subtitle_options", AsyncMock(side_effect=ProviderUnavailable("Unavailable"))
    )
    assert await run_jobs("cinema_subtitles", rounds=1) == ["failed"]
    job = (await db.execute(select(Job).where(Job.kind == "cinema_subtitles"))).scalar_one()
    job.attempts = job.max_attempts - 1
    from app.core.database import utcnow

    job.run_after = utcnow()
    await db.commit()
    assert await run_jobs("cinema_subtitles", rounds=1) == ["dead"]
    assert (await client.get(f"/movies/{movie_id}")).json()["movie"]["subtitle_status"] == "failed"


async def test_invalid_and_oversized_upload(client, movie_provider):
    movie_id = await add(client)
    assert (await upload(client, movie_id, b"<html>not subtitles</html>")).status_code == 422
    assert (
        await upload(client, movie_id, b"x" * (provider.MAX_SUBTITLE_BYTES + 1))
    ).status_code == 413
    detail = (await client.get(f"/movies/{movie_id}")).json()
    assert detail["tracks"] == []


@pytest.mark.parametrize(
    "url",
    [
        "http://subs5.strem.io/file",
        "https://127.0.0.1/test",
        "https://subs5.strem.io.evil.test/a",
        "https://user:pass@subs5.strem.io/a",
        "https://subs5.strem.io:bad/a",
        "https://subs5.strem.io:444/a",
        "https://[broken",
    ],
)
def test_download_allowlist(url):
    assert not provider.safe_subtitle_url(url)


def test_subtitle_formats_and_encoding():
    assert provider.poster_url("https://m.media-amazon.com/images/example.jpg")
    assert provider.poster_url("https://untrusted.example/poster.jpg") is None
    assert provider.safe_subtitle_url("https://subs5.strem.io/en/download/123")
    vtt = "WEBVTT\n\n00:01.250 --> 00:04.500 align:start\n<b>Привет</b> &amp; мир\n"
    for data in (vtt.encode(), vtt.encode("utf-16"), vtt.encode("cp1251")):
        cue = provider.parse_subtitle(data)[0]
        assert cue["start_ms"] == 1250 and cue["end_ms"] == 4500
        assert cue["body"] == "Привет & мир"
    for data in (b"\x98", b"\x00", b"1\n00:00:03,000 --> 00:00:01,000\nBad\n"):
        with pytest.raises(ValidationFailed):
            provider.parse_subtitle(data)
