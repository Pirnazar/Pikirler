"""Public movie metadata and subtitle discovery; no browser cookies or API keys."""

from __future__ import annotations

import gzip
import html
import io
import re
from urllib.parse import quote, urlsplit

import httpx

from app.core.exceptions import ProviderUnavailable, ValidationFailed

MAX_SUBTITLE_BYTES = 5 * 1024 * 1024
IMDB_RE = re.compile(r"tt\d{7,12}\Z")
TIME_RE = re.compile(r"(?:(\d{1,3}):)?(\d{2}):(\d{2})[,.](\d{3})\Z")
TIMING_RE = re.compile(
    r"^\s*((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d{1,3}:)?\d{2}:\d{2}[,.]\d{3})(?:\s.*)?$"
)
LANGUAGES = {"ru": "rus", "en": "eng", "tk": "tuk", "tr": "tur"}


def poster_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.port not in (None, 443):
            return None
    except ValueError:
        return None
    return (
        value
        if url.scheme == "https"
        and url.hostname in {"images.metahub.space", "image.tmdb.org", "m.media-amazon.com"}
        and not url.username
        and not url.password
        else None
    )


async def _provider_data(url: str) -> dict | list:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.get(url, headers={"User-Agent": "Pikirler/0.1"})
            response.raise_for_status()
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError("Response too large")
            result = response.json()
            if not isinstance(result, dict | list):
                raise ValueError("Invalid response")
            return result
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderUnavailable("Movie or subtitle catalog is temporarily unavailable") from exc


async def provider_json(url: str) -> dict:
    result = await _provider_data(url)
    if not isinstance(result, dict):
        raise ProviderUnavailable("Invalid movie or subtitle catalog response")
    return result


def movie_data(meta: dict) -> dict:
    def names(key: str) -> list[str]:
        values = meta.get(key) or []
        if isinstance(values, str):
            values = [values]
        return [str(value)[:200] for value in values[:40] if isinstance(value, str)]

    return {
        "imdb_id": meta.get("imdb_id") or meta.get("id"),
        "title": str(meta.get("name") or "")[:500],
        "year": str(meta.get("year") or meta.get("releaseInfo") or "")[:16] or None,
        "poster": poster_url(meta.get("poster")),
        "description": str(meta.get("description") or "")[:20000] or None,
        "runtime": str(meta.get("runtime") or "")[:80] or None,
        "rating": str(meta.get("imdbRating") or "")[:16] or None,
        "genres": names("genres") or names("genre"),
        "cast": names("cast"),
        "director": names("director"),
    }


async def search_movies(query: str) -> list[dict]:
    data = await provider_json(
        "https://v3-cinemeta.strem.io/catalog/movie/top/search=" + quote(query, safe="") + ".json"
    )
    result = []
    for meta in data.get("metas", [])[:30]:
        if not isinstance(meta, dict) or meta.get("type", "movie") != "movie":
            continue
        item = movie_data(meta)
        if IMDB_RE.fullmatch(str(item["imdb_id"])) and item["title"]:
            result.append(item)
    return result


async def get_movie(imdb_id: str) -> dict:
    if not IMDB_RE.fullmatch(imdb_id):
        raise ValidationFailed("Invalid IMDb ID")
    data = await provider_json(f"https://v3-cinemeta.strem.io/meta/movie/{imdb_id}.json")
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("type", "movie") != "movie":
        raise ValidationFailed("Film not found")
    result = movie_data(meta)
    if result["imdb_id"] != imdb_id or not result["title"]:
        raise ValidationFailed("Film not found")
    return result


async def get_russian_movie(imdb_id: str) -> dict:
    if not IMDB_RE.fullmatch(imdb_id):
        raise ValidationFailed("Invalid IMDb ID")
    data = await provider_json(f"https://tmdb.elfhosted.com/ru-RU/meta/movie/{imdb_id}.json")
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("imdb_id") != imdb_id:
        raise ValidationFailed("Russian metadata does not match the film")
    result = movie_data(meta)
    if not result["title"]:
        raise ValidationFailed("Russian movie title is missing")
    if not result["cast"]:
        result["cast"] = [
            str(link["name"])[:200]
            for link in (meta.get("links") or [])
            if isinstance(link, dict)
            and link.get("category") == "Cast"
            and isinstance(link.get("name"), str)
        ][:40]
    tmdb_id = str(meta.get("id", "")).removeprefix("tmdb:")
    result["source"] = "tmdb"
    runtime = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)min)?", result["runtime"] or "")
    if runtime and any(runtime.groups()):
        result["runtime"] = " ".join(
            filter(
                None,
                (
                    f"{runtime[1]} ч" if runtime[1] else "",
                    f"{runtime[2]} мин" if runtime[2] else "",
                ),
            )
        )
    result["source_url"] = (
        f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id.isdigit() else None
    )
    return result


def safe_subtitle_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (
            re.fullmatch(r"subs\d*\.strem\.io", host)
            or host in {"dl.opensubtitles.org", "dl.opensubtitles.com"}
        )
    )


async def subtitle_options(imdb_id: str, language: str) -> list[dict]:
    """The legacy REST catalog includes RU tracks missing from Stremio v3."""
    if not IMDB_RE.fullmatch(imdb_id):
        raise ValidationFailed("Invalid IMDb ID")
    code = LANGUAGES.get(language, language)
    rest_error = None
    try:
        data = await _provider_data(
            f"https://rest.opensubtitles.org/search/imdbid-{imdb_id[2:]}/sublanguageid-{code}"
        )
        if not isinstance(data, list):
            raise ProviderUnavailable("Invalid subtitle catalog response")
        items = []
        for item in data[:1000]:
            if not isinstance(item, dict) or item.get("SubLanguageID") != code:
                continue
            if str(item.get("IDMovieImdb", "")).lstrip("0") != imdb_id[2:].lstrip("0"):
                continue
            url = item.get("SubDownloadLink")
            if not isinstance(url, str) or not safe_subtitle_url(url):
                continue
            if str(item.get("SubSumCD", "1")) != "1":
                continue
            try:
                downloads = int(item.get("SubDownloadsCnt") or 0)
            except (TypeError, ValueError):
                downloads = 0
            items.append(
                {
                    "id": str(item.get("IDSubtitle", ""))[:64],
                    "language": language,
                    "label": str(item.get("SubFileName") or "OpenSubtitles")[:255],
                    "url": url,
                    "forced": str(item.get("SubForeignPartsOnly", "0")) == "1",
                    "downloads": downloads,
                }
            )
        if items:
            return sorted(items, key=lambda item: (item["forced"], -item["downloads"]))[:30]
    except ProviderUnavailable as exc:
        rest_error = exc
    items = await _stremio_subtitle_options(imdb_id, language)
    if not items and rest_error:
        raise rest_error
    return items


async def _stremio_subtitle_options(imdb_id: str, language: str) -> list[dict]:
    data = await provider_json(f"https://opensubtitles-v3.strem.io/subtitles/movie/{imdb_id}.json")
    code = LANGUAGES.get(language, language)
    items = []
    for item in data.get("subtitles", [])[:1000]:
        if not isinstance(item, dict) or item.get("lang") not in {language, code}:
            continue
        url = item.get("url")
        if isinstance(url, str) and safe_subtitle_url(url):
            items.append(
                {
                    "id": str(item.get("id", ""))[:64],
                    "language": language,
                    "label": str(item.get("label") or f"OpenSubtitles #{item.get('id')}")[:255],
                    "url": url,
                }
            )
    return items[:30]


async def download_subtitle(url: str) -> bytes:
    try:
        return await _download_subtitle(url)
    except (httpx.HTTPError, OSError, EOFError) as exc:
        raise ProviderUnavailable("Subtitle download is temporarily unavailable") from exc


async def _download_subtitle(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for _ in range(4):
            if not safe_subtitle_url(url):
                raise ValidationFailed("Unsupported subtitle download address")
            async with client.stream(
                "GET", url, headers={"User-Agent": "Pikirler/0.1"}
            ) as response:
                if response.is_redirect:
                    url = str(response.url.join(response.headers["location"]))
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_SUBTITLE_BYTES:
                        raise ValidationFailed("Subtitle file is too large")
                result = bytes(data)
                if result.startswith(b"\x1f\x8b"):
                    with gzip.GzipFile(fileobj=io.BytesIO(result)) as stream:
                        result = stream.read(MAX_SUBTITLE_BYTES + 1)
                if len(result) > MAX_SUBTITLE_BYTES:
                    raise ValidationFailed("Subtitle file is too large")
                return result
    raise ValidationFailed("Too many subtitle redirects")


def parse_subtitle(data: bytes) -> list[dict]:
    if not data or len(data) > MAX_SUBTITLE_BYTES:
        raise ValidationFailed("Subtitle file is empty or too large")
    try:
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        decoded = data.decode(encoding)
    except UnicodeError:
        try:
            decoded = data.decode("cp1251")
        except UnicodeError as exc:
            raise ValidationFailed("Unsupported subtitle text encoding") from exc
    if "\x00" in decoded:
        raise ValidationFailed("Expected a text SRT or VTT file")

    def milliseconds(value: str) -> int:
        match = TIME_RE.fullmatch(value)
        assert match
        hours, minutes, seconds, fraction = (int(part or 0) for part in match.groups())
        if minutes >= 60 or seconds >= 60 or hours > 48:
            raise ValidationFailed("Invalid subtitle timestamp")
        return ((hours * 60 + minutes) * 60 + seconds) * 1000 + fraction

    lines = decoded.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    cues = []
    index = 0
    while index < len(lines):
        timing = TIMING_RE.fullmatch(lines[index])
        index += 1
        if not timing:
            continue
        body = []
        while index < len(lines) and lines[index].strip():
            if TIMING_RE.fullmatch(lines[index]):
                break
            body.append(lines[index])
            index += 1
        clean = " ".join(html.unescape(re.sub(r"<[^>]*>|\{\\[^}]*\}", "", " ".join(body))).split())
        if not clean:
            continue
        start, end = milliseconds(timing[1]), milliseconds(timing[2])
        if end < start:
            raise ValidationFailed("Subtitle end precedes its start")
        cues.append(
            {"position": len(cues), "start_ms": start, "end_ms": end, "body": clean[:12000]}
        )
        if len(cues) > 50000:
            raise ValidationFailed("Too many subtitle cues")
    if not cues:
        raise ValidationFailed("No timestamped subtitles found; use SRT or VTT")
    return cues
