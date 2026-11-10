"""Saved copies of linked pages, against fake sites (httpx.MockTransport)."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import httpx
import pytest
from PIL import Image
from sqlalchemy import select, update

from app.core.database import get_database, utcnow
from app.modules.pikirler.integrations import pages, web
from app.modules.pikirler.models import Job, LinkSnapshot
from app.modules.pikirler.services.links import extract_urls
from app.storage import get_storage
from tests.conftest import new_thought
from tests.helpers import run_jobs

PARAGRAPHS = [
    "Bu makalada ýatda saklamak üçin möhüm pikirler bar. Her abzas ýeterlik uzyn bolmaly, "
    "sebäbi okalýan bölegi saýlaýan gural gysga ýazgylary taşlaýar.",
    "Ikinji abzas: ýollar, daglar we deňiz barada gürrüň berilýär. Aýratyn söz: "
    "zümerret, gözleg muny tapmaly.",
    "Üçünji abzas makalanyň soňy bolup durýar we ähli zatlary jemleýär. "
    "Okyjy bu ýerde netijäni görýär we makala tamamlanýar.",
]
ARTICLE = f"""<!doctype html><html><head>
<title>Ýol barada | Mysal Neşir</title>
<meta property="og:title" content="Ýol barada">
<meta property="og:site_name" content="Mysal Neşir">
<meta property="og:image" content="/cover.png">
<meta name="author" content="Aýna Myradowa">
<meta property="article:published_time" content="2026-11-01T09:00:00Z">
</head><body>
<nav><a href="/">Baş sahypa</a> <a href="/about">Biz barada</a></nav>
<article><h1>Ýol barada</h1>
{"".join(f"<p>{p}</p>" for p in PARAGRAPHS)}
</article>
<footer>© Mysal Neşir — ähli hukuklar goralan</footer>
</body></html>"""


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (20, 40, 120)).save(buf, format="PNG")
    return buf.getvalue()


TESWIRLER_BLOG = {
    "data": {
        "id": "f33b",
        "slug": "watan-nireden-baslanyar",
        "title": "Watan nireden başlanýar?!",
        "excerpt": "Eziz watan şirin jandan ybarat...",
        "coverImage": "https://cdn.example.org/cover.png",
        "publishedAt": "2026-11-10T12:07:55.725Z",
        "author": {"name": "Garaşylmadyk)", "username": "Ejegyzzz"},
        "body": "<p>Eziz watan şirin jandan ybarat,<br>Damarda gaýnaýan gandan ybarat.</p>"
        "<p><strong>Türkmen</strong> watan bilen diridir.</p>",
    }
}


def _comment(cid: str, who: str, text: str, minute: int, replies: int = 0) -> dict:  # type: ignore[type-arg]
    return {
        "id": cid,
        "content": text,
        "quote": None,
        "createdAt": f"2026-11-10T12:{minute:02d}:00.000Z",
        "author": {"name": who.title(), "username": who},
        "likesCount": minute % 5,
        "repliesCount": replies,
    }


BLOG_COMMENTS = [  # newest first, as Teswirler sends them
    _comment("c2", "aman", "Ajaýyp goşgy!", 30),
    _comment("c1", "jeren", "Kimiň goşgusy?", 16, replies=1),
]
BLOG_REPLIES = {"c1": [_comment("r1", "ejegyzzz", "Meniňki, sag boluň", 19)]}
TESWIRLER_PIKIR = {
    "data": {
        "id": "p1",
        "content": "Bu gysga pikir.\nIkinji setir.",
        "createdAt": "2026-11-10T14:58:53.689Z",
        "updatedAt": "2026-11-10T15:02:45.081Z",
        "author": {"name": "Didar", "username": "Didar"},
        "images": [{"url": "https://cdn.example.org/p1.jpg", "type": "IMAGE"}],
    }
}
# 60 comments on the pikir: two pages of 50 + 10.
PIKIR_COMMENTS = [_comment(f"pc{i}", f"user{i}", f"Teswir {i}", i % 60) for i in range(60)]

X_OEMBED = {
    "url": "https://x.com/aynamyrat/status/1855000000000000000",
    "author_name": "Aýna Myradowa",
    "author_url": "https://x.com/aynamyrat",
    "html": '<blockquote class="twitter-tweet" data-dnt="true"><p lang="tk" dir="ltr">'
    'Salam, dünýä!<br>Ikinji setir &amp; <a href="https://t.co/x">pic.x.com/x</a></p>'
    "&mdash; Aýna Myradowa (@aynamyrat) "
    '<a href="https://x.com/aynamyrat/status/1855000000000000000">November 8, 2024</a>'
    "</blockquote>",
    "provider_name": "X",
}

YT_PLAYER = {
    "playabilityStatus": {"status": "OK"},
    "videoDetails": {
        "videoId": "dQw4w9WgXcQ",
        "title": "Aşgabat gijesi",
        "author": "Merdan TV",
        "lengthSeconds": "212",
        "shortDescription": "Gijeki şäher.\n\nSurata düşüren: Merdan.",
        "thumbnail": {"thumbnails": [{"url": "https://cdn.example.org/yt.png"}]},
    },
    "microformat": {
        "playerMicroformatRenderer": {
            "publishDate": "2026-11-01T08:00:00-07:00",
            "ownerProfileUrl": "http://www.youtube.com/@merdantv",
            "ownerChannelName": "Merdan TV",
        }
    },
}
YT_PAGE = (
    "<html><head><title>x</title></head><body><script>var ytInitialPlayerResponse = "
    + json.dumps(YT_PLAYER)
    + ";var meta = 1;</script></body></html>"
)
YT_IDS = {"dQw4w9WgXcQ"}
YT_GONE = (
    "<html><body><script>var ytInitialPlayerResponse = "
    + json.dumps({"playabilityStatus": {"status": "ERROR", "reason": "Video unavailable"}})
    + ";</script></body></html>"
)
TT_DATA = {
    "__DEFAULT_SCOPE__": {
        "webapp.video-detail": {
            "statusCode": 0,
            "itemInfo": {
                "itemStruct": {
                    "id": "7300000000000000000",
                    "desc": "Halyçy eje #türkmen",
                    "createTime": "1731000000",
                    "author": {"uniqueId": "halyci", "nickname": "Halyçy"},
                    "video": {"cover": "https://cdn.example.org/tt.png", "duration": 31},
                }
            },
        }
    }
}
TT_PAGE = (
    '<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'
    + json.dumps(TT_DATA)
    + "</script></body></html>"
)

PUBLIC = {
    "news.example.org": "93.184.216.34",
    "cdn.example.org": "93.184.216.35",
    "api.teswirler.com": "104.21.1.1",
    "teswirler.com": "104.21.1.2",
    "sneaky.example.org": "10.0.0.5",
    "moved.example.org": "93.184.216.36",
    "publish.x.com": "104.244.42.1",
    "www.youtube.com": "142.250.1.1",
    "www.tiktok.com": "23.1.1.1",
    "www.instagram.com": "157.240.1.1",
}


class FakeWeb:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.status: dict[str, int] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if url in self.status:
            return httpx.Response(self.status[url], text="nope")
        host, path = request.url.host, request.url.path
        if host == "news.example.org" and path == "/yol":
            return httpx.Response(
                200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"}
            )
        if host == "news.example.org" and path == "/cover.png":
            return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
        if host == "cdn.example.org":
            return httpx.Response(200, content=png_bytes(), headers={"content-type": "image/png"})
        if host == "api.teswirler.com" and path == "/blogs/watan-nireden-baslanyar":
            return httpx.Response(200, json=TESWIRLER_BLOG)
        if host == "api.teswirler.com" and path == "/blogs/f33b/comments":
            return httpx.Response(200, json={"data": BLOG_COMMENTS})
        if host == "api.teswirler.com" and path.startswith("/blogs/f33b/comments/"):
            cid = path.split("/")[4]
            return httpx.Response(
                200, json={"data": {"items": BLOG_REPLIES.get(cid, []), "hasMore": False}}
            )
        if host == "api.teswirler.com" and path == "/pikirler/p1":
            return httpx.Response(200, json=TESWIRLER_PIKIR)
        if host == "api.teswirler.com" and path == "/pikirler/p1/comments":
            skip, take = int(request.url.params["skip"]), int(request.url.params["take"])
            assert take <= 50
            chunk = PIKIR_COMMENTS[skip : skip + take]
            more = skip + take < len(PIKIR_COMMENTS)
            return httpx.Response(200, json={"data": {"items": chunk, "hasMore": more}})
        if host == "publish.x.com" and path == "/oembed":
            target = request.url.params["url"]
            if target == "https://x.com/aynamyrat/status/1855000000000000000":
                return httpx.Response(200, json=X_OEMBED)
            return httpx.Response(404, json={"error": "not found"})
        if host == "www.youtube.com" and path == "/watch":
            vid = request.url.params["v"]
            page = YT_PAGE if vid in YT_IDS else YT_GONE
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        if host == "www.tiktok.com" and path.startswith("/@halyci/video/"):
            return httpx.Response(200, text=TT_PAGE, headers={"content-type": "text/html"})
        if host == "www.instagram.com":
            return httpx.Response(
                200, text="<html><body>Log in</body></html>", headers={"content-type": "text/html"}
            )
        if host == "moved.example.org":
            return httpx.Response(302, headers={"location": "http://192.168.1.8/admin"})
        if host == "news.example.org" and path == "/file.zip":
            return httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})
        return httpx.Response(404, text="not found")


@pytest.fixture
def fake_web() -> Iterator[FakeWeb]:
    fake = FakeWeb()

    async def resolve(host: str, port: int) -> list[str]:
        if host not in PUBLIC:
            raise web.Unreachable("no such host", code="link_dns")
        return [PUBLIC[host]]

    web.set_transport(httpx.MockTransport(fake.handler))
    web.set_resolver(resolve)
    yield fake
    web.set_transport(None)
    web.set_resolver(None)


async def _link(client: httpx.AsyncClient, thought_id: str) -> dict:  # type: ignore[type-arg]
    thought = (await client.get(f"/thoughts/{thought_id}")).json()
    assert len(thought["links"]) == 1, thought["links"]
    return thought["links"][0]  # type: ignore[no-any-return]


def test_extract_urls() -> None:
    text = (
        "Oka: https://news.example.org/yol. Hem (https://teswirler.com/watan), "
        "https://news.example.org/yol again, and «https://x.example.org/a?b=1»!"
    )
    assert extract_urls(text, limit=5) == [
        "https://news.example.org/yol",
        "https://teswirler.com/watan",
        "https://x.example.org/a?b=1",
    ]
    assert extract_urls(text, limit=1) == ["https://news.example.org/yol"]


def test_html_fragment_to_text() -> None:
    text = pages.html_to_text(
        "<p>Bir,<br>iki.</p><script>x()</script><ul><li>a</li><li>b</li></ul>"
    )
    assert text == "Bir,\niki.\n\n• a\n• b"


def test_teswirler_paths() -> None:
    assert pages.teswirler_api_path("https://teswirler.com/watan-nireden-baslanyar") == (
        "/blogs/watan-nireden-baslanyar"
    )
    assert pages.teswirler_api_path("https://teswirler.com/pikirler/86ae") == "/pikirler/86ae"
    assert pages.teswirler_api_path("https://teswirler.com/bildirisler") is None
    assert pages.teswirler_api_path("https://teswirler.com/@pirnazar") is None
    assert pages.teswirler_api_path("https://example.com/watan") is None


async def test_article_is_saved_with_text_and_cover(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "Gyzykly makala https://news.example.org/yol")
    [pending] = created["links"]
    assert pending["status"] == "pending"
    assert pending["url"] == "https://news.example.org/yol"
    await run_jobs()

    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["title"] == "Ýol barada"
    assert link["site_name"] == "Mysal Neşir"
    assert link["author"] == "Aýna Myradowa"
    assert link["published_at"].startswith("2026-11-01")
    assert link["reading_minutes"] == 1
    assert link["cover_url"] == f"/api/v1/pikirler/links/{link['id']}/cover"

    detail = (await client.get(f"/links/{link['id']}")).json()
    assert "zümerret" in detail["text"]
    assert "Biz barada" not in detail["text"]  # menus are dropped
    assert "ähli hukuklar" not in detail["text"]  # and footers

    cover = await client.get(f"/links/{link['id']}/cover")
    assert cover.status_code == 200
    assert cover.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(cover.content)) as img:
        assert max(img.size) == 1200

    # The saved text is searchable.
    found = (await client.get("/search", params={"q": "zümerret"})).json()
    assert [r["thought_id"] for r in found["results"]] == [created["id"]]


async def test_teswirler_article_comes_from_its_api(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["site_name"] == "Teswirler"
    assert link["title"] == "Watan nireden başlanýar?!"
    assert link["author"] == "Garaşylmadyk)"
    assert link["cover_url"]
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert detail["text"] == (
        "Eziz watan şirin jandan ybarat,\nDamarda gaýnaýan gandan ybarat.\n\n"
        "Türkmen watan bilen diridir."
    )
    assert "https://api.teswirler.com/blogs/watan-nireden-baslanyar" in fake_web.calls
    assert not any(c.startswith("https://teswirler.com/") for c in fake_web.calls)

    assert link["kind"] == "article"
    assert link["author_username"] == "Ejegyzzz"
    assert link["author_url"] == "https://teswirler.com/@Ejegyzzz"
    assert link["published_at"] == "2026-11-10T12:07:55.725000Z"
    assert link["comment_count"] == 3
    # Oldest first, replies nested under their comment.
    [first, second] = detail["comments"]
    assert (first["author"], first["username"], first["text"]) == (
        "Jeren",
        "jeren",
        "Kimiň goşgusy?",
    )
    assert first["created_at"] == "2026-11-10T12:16:00Z"
    assert first["author_url"] == "https://teswirler.com/@jeren"
    assert [r["text"] for r in first["replies"]] == ["Meniňki, sag boluň"]
    assert second["text"] == "Ajaýyp goşgy!"
    # Comments are searchable too.
    found = (await client.get("/search", params={"q": "Ajaýyp"})).json()
    assert [r["thought_id"] for r in found["results"]] == [created["id"]]


async def test_teswirler_pikir_is_a_quote_with_all_comments(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    url = "https://teswirler.com/pikirler/p1?utm_source=copy&utm_medium=social"
    created = await new_thought(client, url)
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["kind"] == "post"
    assert link["author"] == "Didar"
    assert link["author_username"] == "Didar"
    assert link["post_text"] == "Bu gysga pikir.\nIkinji setir."
    assert link["edited_at"] is not None
    assert link["cover_url"]
    assert link["comment_count"] == 60  # both pages were read
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert len(detail["comments"]) == 60
    assert detail["comments"][0]["text"] == "Teswir 0"


async def test_comments_failing_keep_the_article(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    fake_web.status["https://api.teswirler.com/blogs/f33b/comments?skip=0&take=50"] = 503
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["comment_count"] is None


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/secret",
        "http://192.168.1.8:9000/bucket",
        "http://localhost/x",
        "http://rustfs:9000/x",
        "https://sneaky.example.org/x",  # resolves to a private address
        "https://moved.example.org/x",  # redirects into the LAN
        "https://user:pw@news.example.org/yol",
    ],
)
async def test_private_addresses_are_never_fetched(
    client: httpx.AsyncClient, fake_web: FakeWeb, url: str
) -> None:
    created = await new_thought(client, f"Barla {url}")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "failed"
    assert link["error_code"] == "link_blocked"
    assert not any("192.168" in c or "127.0.0.1" in c or "rustfs" in c for c in fake_web.calls)


async def test_missing_page_fails_without_retry(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "https://news.example.org/yok")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "failed"
    assert link["error_code"] == "link_not_found"
    assert len(fake_web.calls) == 1


async def test_not_a_page(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    created = await new_thought(client, "https://news.example.org/file.zip")
    await run_jobs()
    assert (await _link(client, created["id"]))["error_code"] == "link_unsupported"


async def test_unreachable_site_is_retried_then_failed(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    url = "https://news.example.org/yol"
    fake_web.status[url] = 503
    created = await new_thought(client, url)
    await run_jobs()
    assert (await _link(client, created["id"]))["status"] == "pending"  # a retry is scheduled
    async with get_database().sessionmaker() as db:
        await db.execute(
            update(Job).where(Job.kind == "fetch_link").values(run_after=utcnow(), attempts=3)
        )
        await db.commit()
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "failed"
    assert link["error_code"] == "link_unreachable"

    # The site is back: fetch it again on request.
    del fake_web.status[url]
    again = await client.post(f"/links/{link['id']}/refetch")
    assert again.json()["status"] == "pending"
    await run_jobs()
    assert (await _link(client, created["id"]))["status"] == "ready"


async def test_editing_drops_removed_links_and_their_covers(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    async with get_database().sessionmaker() as db:
        key = (await db.execute(select(LinkSnapshot.cover_key))).scalar_one()
    assert key and await get_storage().head(key) is not None

    resp = await client.patch(
        f"/thoughts/{created['id']}",
        json={
            "content": "Indi başga: https://teswirler.com/watan-nireden-baslanyar",
            "expected_version": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    assert [x["url"] for x in resp.json()["links"]] == [
        "https://teswirler.com/watan-nireden-baslanyar"
    ]
    await run_jobs()
    assert await get_storage().head(key) is None
    found = (await client.get("/search", params={"q": "zümerret"})).json()
    assert found["results"] == []


async def test_unchanged_links_are_not_fetched_again(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    calls = len(fake_web.calls)
    await client.patch(
        f"/thoughts/{created['id']}",
        json={"content": "Täze söz. https://news.example.org/yol", "expected_version": 1},
    )
    await run_jobs()
    assert len(fake_web.calls) == calls
    assert (await _link(client, created["id"]))["status"] == "ready"


async def test_saved_pages_are_private_to_the_owner(
    client: httpx.AsyncClient, other_client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    created = await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    link = await _link(client, created["id"])
    for path in (f"/links/{link['id']}", f"/links/{link['id']}/cover"):
        assert (await other_client.get(path)).status_code == 404
    assert (await other_client.post(f"/links/{link['id']}/refetch")).status_code == 404


async def test_export_includes_saved_pages(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    resp = await client.get("/export/json")
    assert resp.status_code == 200
    doc = json.loads(resp.content)
    [page] = doc["thoughts"][0]["saved_pages"]
    assert page["title"] == "Ýol barada"
    assert "zümerret" in page["text"]


async def test_export_includes_comments(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    doc = json.loads((await client.get("/export/json")).content)
    [page] = doc["thoughts"][0]["saved_pages"]
    assert page["author_username"] == "Ejegyzzz"
    assert [c["text"] for c in page["comments"]] == ["Kimiň goşgusy?", "Ajaýyp goşgy!"]
    from app.modules.pikirler.services.data_export import thought_markdown

    _, md = thought_markdown(doc["thoughts"][0], "UTC")
    assert "- **Jeren (@jeren)**, 2026-11-10T12:16:00+00:00: Kimiň goşgusy?" in md
    assert "  - **Ejegyzzz (@ejegyzzz)**" in md


# --- keeping copies in step with the site -----------------------------------------


async def _make_due() -> None:
    """Pretend the last check was long ago, so the scheduler picks the copies."""
    from datetime import timedelta

    async with get_database().sessionmaker() as db:
        await db.execute(update(LinkSnapshot).values(synced_at=utcnow() - timedelta(days=8)))
        await db.commit()


async def _sync_now() -> int:
    from app.modules.pikirler.services import links

    await _make_due()
    async with get_database().sessionmaker() as db:
        queued = await links.schedule_sync(db)
    await run_jobs("sync_link")
    return queued


def _flat(comments: list) -> dict:  # type: ignore[type-arg]
    out = {}
    for c in comments:
        out[c["text"]] = c["deleted_at"]
        out.update(_flat(c["replies"]))
    return out


def test_merge_comments_marks_only_with_a_complete_list() -> None:
    from app.modules.pikirler.services.links import count_comments, merge_comments

    old = [
        {
            "id": "a",
            "text": "A",
            "created_at": "1",
            "replies": [{"id": "a1", "text": "A1", "created_at": "2"}],
        },
        {"id": "b", "text": "B", "created_at": "3", "replies": []},
    ]
    new = [{"id": "a", "text": "A edited", "created_at": "1", "replies": []}]
    partial = merge_comments(old, new, complete=False, when="T")
    assert count_comments(partial) == (3, 0)
    merged = merge_comments(old, new, complete=True, when="T")
    assert [c["text"] for c in merged] == ["A edited", "B"]
    assert merged[0]["replies"][0]["deleted_at"] == "T"
    assert merged[1]["deleted_at"] == "T"
    assert count_comments(merged) == (1, 2)
    # Deleted comments keep the date they were first found missing.
    again = merge_comments(merged, new, complete=True, when="LATER")
    assert again[1]["deleted_at"] == "T"
    # And come back to life if the site shows them again.
    back = merge_comments(again, [*new, {"id": "b", "text": "B", "created_at": "3"}], True, "X")
    assert back[1]["deleted_at"] is None


async def test_sync_adds_new_comments_and_marks_deleted_ones(
    client: httpx.AsyncClient, fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    assert (await _link(client, created["id"]))["comment_count"] == 3

    # On the site: "Ajaýyp goşgy!" and the reply were deleted, a new thread came.
    monkeypatch.setitem(
        globals(),
        "BLOG_COMMENTS",
        [_comment("c3", "maral", "Täze teswir", 45, replies=1), BLOG_COMMENTS[1]],
    )
    monkeypatch.setitem(
        globals(), "BLOG_REPLIES", {"c1": [], "c3": [_comment("r3", "aman", "Jogap", 50)]}
    )
    monkeypatch.setitem(globals()["BLOG_COMMENTS"][1], "repliesCount", 0)
    assert await _sync_now() == 1

    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["comment_count"] == 3  # c1, c3 and its reply
    assert link["deleted_comment_count"] == 2
    assert link["synced_at"] is not None
    detail = (await client.get(f"/links/{link['id']}")).json()
    state = _flat(detail["comments"])
    assert state["Kimiň goşgusy?"] is None
    assert state["Täze teswir"] is None
    assert state["Jogap"] is None
    assert state["Ajaýyp goşgy!"] is not None  # kept, marked deleted
    assert state["Meniňki, sag boluň"] is not None


async def test_failed_sync_changes_nothing(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    fake_web.status["https://api.teswirler.com/blogs/f33b/comments/c1/replies?skip=0&take=50"] = 503
    await _sync_now()
    link = await _link(client, created["id"])
    assert (link["comment_count"], link["deleted_comment_count"]) == (3, 0)


async def test_deleted_original_keeps_the_copy(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    api = "https://api.teswirler.com/blogs/watan-nireden-baslanyar"
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    fake_web.status[api] = 404
    await _sync_now()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["source_deleted_at"] is not None
    assert link["title"] == "Watan nireden başlanýar?!"
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert "Türkmen watan" in detail["text"]

    # Asking for a refresh does not throw the copy away either.
    await client.post(f"/links/{link['id']}/refetch")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["source_deleted_at"] is not None

    # The original is back: the mark goes away.
    del fake_web.status[api]
    await _sync_now()
    assert (await _link(client, created["id"]))["source_deleted_at"] is None


async def test_only_due_teswirler_copies_are_synced(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    from app.modules.pikirler.services import links

    await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await new_thought(client, "https://news.example.org/yol")  # not a site we sync
    await run_jobs()
    async with get_database().sessionmaker() as db:
        assert await links.schedule_sync(db) == 0  # just saved: not due yet
    assert await _sync_now() == 1  # only the Teswirler copy


def test_x_status_links() -> None:
    assert pages.x_status("https://x.com/jack/status/20") == ("jack", "20")
    assert pages.x_status("https://twitter.com/jack/status/20?s=20&t=abc") == ("jack", "20")
    assert pages.x_status("https://mobile.twitter.com/jack/status/20/photo/1") == ("jack", "20")
    assert pages.x_status("https://x.com/jack") is None
    assert pages.x_status("https://example.com/jack/status/20") is None


async def test_x_post_is_saved_as_a_quote(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    created = await new_thought(
        client, "https://twitter.com/aynamyrat/status/1855000000000000000?s=20"
    )
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["kind"] == "post"
    assert link["site_name"] == "X"
    assert link["author"] == "Aýna Myradowa"
    assert link["author_username"] == "aynamyrat"
    assert link["author_url"] == "https://x.com/aynamyrat"
    assert link["post_text"] == "Salam, dünýä!\nIkinji setir & pic.x.com/x"
    # The exact time comes from the post id, not the day-only date in the embed.
    assert link["published_at"].startswith("2024-11-")
    assert link["comment_count"] is None  # X does not give replies for free
    # Only X's official embed endpoint was asked, never the x.com page.
    assert all(c.startswith("https://publish.x.com/oembed?") for c in fake_web.calls)


async def test_deleted_x_post_is_marked(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    created = await new_thought(client, "https://x.com/aynamyrat/status/1855000000000000000")
    await run_jobs()
    fake_web.status[
        "https://publish.x.com/oembed?url=https%3A%2F%2Fx.com%2Faynamyrat%2Fstatus%2F1855000000000000000"
        "&omit_script=1&dnt=1&lang=en"
    ] = 404
    await _sync_now()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert link["source_deleted_at"] is not None
    assert link["post_text"].startswith("Salam, dünýä!")


async def test_missing_x_post_fails_cleanly(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    created = await new_thought(client, "https://x.com/nobody/status/1")
    await run_jobs()
    link = await _link(client, created["id"])
    assert (link["status"], link["error_code"]) == ("failed", "link_not_found")


# --- YouTube, TikTok, Instagram: description and the video itself ----------------------


@pytest.fixture
def fake_video() -> Iterator[list[str]]:
    """Downloads write a small file instead of using the network."""
    from app.modules.pikirler.integrations import media_download

    asked: list[str] = []

    def download(url: str, workdir: str) -> media_download.Downloaded:
        asked.append(url)
        path = f"{workdir}/media.mp4"
        with open(path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2000)
        return media_download.Downloaded(path=path, mime_type="video/mp4", filename="video.mp4")

    media_download.set_downloader(download)
    yield asked
    media_download.set_downloader(None)


def test_video_site_links() -> None:
    from app.modules.pikirler.integrations import video_sites as v

    assert v.youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10") == "dQw4w9WgXcQ"
    assert v.youtube_id("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
    assert v.youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert v.youtube_id("https://www.youtube.com/@merdantv") is None
    assert v.is_tiktok("https://vm.tiktok.com/ZMabc/")
    assert v.is_instagram_post("https://www.instagram.com/reel/C5yBwOqoLZ7/?igsh=x")
    assert not v.is_instagram_post("https://www.instagram.com/merdan/")


async def test_youtube_video_with_description_and_file(
    client: httpx.AsyncClient, fake_web: FakeWeb, fake_video: list[str]
) -> None:
    created = await new_thought(client, "Gör: https://youtu.be/dQw4w9WgXcQ?si=share")
    await run_jobs("fetch_link")
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert (link["site_name"], link["title"], link["author"]) == (
        "YouTube",
        "Aşgabat gijesi",
        "Merdan TV",
    )
    assert link["author_username"] == "merdantv"
    assert link["author_url"] == "https://www.youtube.com/@merdantv"
    assert link["published_at"].startswith("2026-11-01T15:00:00")
    assert link["duration_seconds"] == 212
    assert link["comment_count"] is None  # no comments for video sites
    assert link["media_status"] == "pending"
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert detail["text"] == "Gijeki şäher.\n\nSurata düşüren: Merdan."

    await run_jobs("download_link_media")
    assert fake_video == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
    link = await _link(client, created["id"])
    assert link["media_status"] == "ready"
    assert link["media"]["mime_type"] == "video/mp4"
    video = await client.get(link["media"]["content_url"].removeprefix("/api/v1/pikirler"))
    assert video.status_code in (200, 206)
    assert video.content.startswith(b"\x00\x00\x00\x18ftyp")

    # The file belongs to the saved page: housekeeping does not treat it as orphaned.
    from datetime import timedelta

    from app.modules.pikirler.models import Attachment
    from app.modules.pikirler.services import maintenance

    async with get_database().sessionmaker() as db:
        await db.execute(update(Attachment).values(uploaded_at=utcnow() - timedelta(days=30)))
        await db.commit()
        await maintenance.cleanup_media(db)
    assert (await _link(client, created["id"]))["media"] is not None

    # A background check does not download it again.
    await _sync_now()
    await run_jobs("download_link_media")
    assert len(fake_video) == 1


async def test_tiktok_video(
    client: httpx.AsyncClient, fake_web: FakeWeb, fake_video: list[str]
) -> None:
    created = await new_thought(client, "https://www.tiktok.com/@halyci/video/7300000000000000000")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert (link["site_name"], link["kind"], link["author"], link["author_username"]) == (
        "TikTok",
        "post",
        "Halyçy",
        "halyci",
    )
    assert link["post_text"] == "Halyçy eje #türkmen"
    assert link["published_at"].startswith("2024-11-07")
    assert link["author_url"] == "https://www.tiktok.com/@halyci"
    assert link["media_status"] == "ready"


async def test_removed_youtube_video(
    client: httpx.AsyncClient, fake_web: FakeWeb, fake_video: list[str]
) -> None:
    created = await new_thought(client, "https://www.youtube.com/watch?v=aaaaaaaaaaa")
    await run_jobs()
    link = await _link(client, created["id"])
    assert (link["status"], link["error_code"]) == ("failed", "link_not_found")
    assert fake_video == []


async def test_instagram_behind_login(
    client: httpx.AsyncClient, fake_web: FakeWeb, fake_video: list[str]
) -> None:
    created = await new_thought(client, "https://www.instagram.com/p/C5yBwOqoLZ7/")
    await run_jobs()
    link = await _link(client, created["id"])
    assert (link["status"], link["error_code"]) == ("failed", "link_login_required")


async def test_failed_download_keeps_the_page(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    from app.modules.pikirler.integrations import media_download

    def refuse(url: str, workdir: str) -> media_download.Downloaded:
        raise media_download.MediaError("Sign in to confirm", code="media_login_required")

    media_download.set_downloader(refuse)
    try:
        created = await new_thought(client, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        await run_jobs()
    finally:
        media_download.set_downloader(None)
    link = await _link(client, created["id"])
    assert link["status"] == "ready"
    assert (link["media_status"], link["media_error"]) == ("failed", "media_login_required")
    assert link["title"] == "Aşgabat gijesi"


def test_download_options_accept_short_and_unknown_length_videos() -> None:
    """yt-dlp's match filter: short or unknown length passes, too long is skipped."""
    from yt_dlp.utils import match_filter_func

    from app.core.config import get_settings

    limit = get_settings().link_media_max_minutes * 60
    keep = match_filter_func([f"duration < {limit}", "!duration"])
    assert keep({"duration": 19}) is None
    assert keep({"id": "x"}) is None
    assert keep({"duration": limit + 1}) is not None


# --- one copy per page, new comments, summaries, downloader health -----------------------


def test_canonical_urls() -> None:
    from app.modules.pikirler.services.link_keys import canonical_url as key

    assert key("https://youtu.be/dQw4w9WgXcQ?si=x") == key(
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share"
    )
    assert key("https://twitter.com/old_name/status/20?s=20") == key("https://x.com/new/status/20")
    assert key("https://www.teswirler.com/pikirler/p1?utm_source=copy") == (
        "https://teswirler.com/pikirler/p1"
    )
    assert key("https://Example.org/a/?utm_medium=x&id=3#top") == "https://example.org/a?id=3"
    assert key("https://example.org/a?id=3") != key("https://example.org/a?id=4")


async def test_two_thoughts_share_one_copy(client: httpx.AsyncClient, fake_web: FakeWeb) -> None:
    first = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    calls = len(fake_web.calls)
    second = await new_thought(
        client, "Ýene: https://www.teswirler.com/watan-nireden-baslanyar?utm_source=copy"
    )
    # Already saved: ready at once, nothing fetched again.
    [link] = second["links"]
    assert link["status"] == "ready"
    assert link["url"] == "https://www.teswirler.com/watan-nireden-baslanyar?utm_source=copy"
    await run_jobs()
    assert len(fake_web.calls) == calls
    assert link["id"] == (await _link(client, first["id"]))["id"]
    async with get_database().sessionmaker() as db:
        assert len(list((await db.execute(select(LinkSnapshot))).scalars())) == 1
        key = (await db.execute(select(LinkSnapshot.cover_key))).scalar_one()

    # Removing the link from one thought keeps the copy for the other.
    await client.patch(f"/thoughts/{first['id']}", json={"content": "Başga", "expected_version": 1})
    await run_jobs()
    assert (await _link(client, second["id"]))["status"] == "ready"
    assert await get_storage().head(key) is not None
    # The search still finds the page through the thought that links to it.
    found = (await client.get("/search", params={"q": "diridir"})).json()
    assert [r["thought_id"] for r in found["results"]] == [second["id"]]

    # When no thought links to it any more, the copy and its files go.
    await client.patch(f"/thoughts/{second['id']}", json={"content": "Hiç", "expected_version": 1})
    await run_jobs()
    async with get_database().sessionmaker() as db:
        assert list((await db.execute(select(LinkSnapshot))).scalars()) == []
    assert await get_storage().head(key) is None


async def test_new_comments_until_seen(
    client: httpx.AsyncClient, fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await new_thought(client, "https://teswirler.com/watan-nireden-baslanyar")
    await run_jobs()
    link = await _link(client, created["id"])
    assert link["new_comment_count"] == 0  # the first download is not "new"

    monkeypatch.setitem(
        globals(), "BLOG_COMMENTS", [_comment("c9", "maral", "Täze teswir", 59), *BLOG_COMMENTS]
    )
    await _sync_now()
    link = await _link(client, created["id"])
    assert link["new_comment_count"] == 1
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert [c["text"] for c in detail["comments"] if c["is_new"]] == ["Täze teswir"]

    seen = await client.post(f"/links/{link['id']}/seen")
    assert seen.status_code == 200
    assert seen.json()["new_comment_count"] == 0
    assert (await _link(client, created["id"]))["new_comment_count"] == 0


async def test_summary_is_made_once_per_language(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    from app.modules.pikirler.integrations import llm
    from tests.fakes import FakeLLM

    created = await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    link = await _link(client, created["id"])

    fake = FakeLLM(
        respond=lambda m: {"gist": "Ýol barada gysgaça.", "points": ["Daglar", "Deňiz", "Netije"]}
    )
    llm.set_llm_provider(fake)
    first = await client.post(f"/links/{link['id']}/summary", json={"language": "tk"})
    assert first.status_code == 200, first.text
    assert first.json()["gist"] == "Ýol barada gysgaça."
    assert first.json()["points"] == ["Daglar", "Deňiz", "Netije"]
    assert len(fake.calls) == 1
    # The page text went to the model as data, with the requested language.
    sent = fake.calls[0]
    assert "zümerret" in sent[1].content
    assert "Turkmen" in sent[0].content

    again = await client.post(f"/links/{link['id']}/summary", json={"language": "tk"})
    assert again.json()["gist"] == "Ýol barada gysgaça."
    assert len(fake.calls) == 1  # kept, not asked again
    assert (await _link(client, created["id"]))["has_summary"] is True
    await client.post(f"/links/{link['id']}/summary", json={"language": "ru"})
    assert len(fake.calls) == 2  # another language: a new summary
    detail = (await client.get(f"/links/{link['id']}")).json()
    assert detail["summary"]["language"] == "ru"


async def test_summary_needs_consent_for_external_ai(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    from app.modules.pikirler.integrations import llm
    from tests.fakes import FakeLLM

    created = await new_thought(client, "https://news.example.org/yol")
    await run_jobs()
    link = await _link(client, created["id"])
    fake = FakeLLM(respond=lambda m: {"gist": "x", "points": []}, is_external=True)
    llm.set_llm_provider(fake)
    resp = await client.post(f"/links/{link['id']}/summary", json={"language": "ru"})
    assert resp.status_code == 403
    assert fake.calls == []


async def test_media_health_reports_a_broken_downloader(
    client: httpx.AsyncClient, fake_web: FakeWeb
) -> None:
    from app.modules.pikirler.integrations import media_download

    health = (await client.get("/links/media-health")).json()
    assert health["broken"] is False
    assert health["ytdlp_version"]

    def broken(url: str, workdir: str) -> media_download.Downloaded:
        raise media_download.MediaError("Unable to extract", code="media_failed")

    media_download.set_downloader(broken)
    try:
        for video in ("dQw4w9WgXcQ", "aaaaaaaaaab", "aaaaaaaaaac"):
            YT_IDS.add(video)
            await new_thought(client, f"https://www.youtube.com/watch?v={video}")
        await run_jobs()
    finally:
        media_download.set_downloader(None)
    health = (await client.get("/links/media-health")).json()
    assert health["broken"] is True
    assert [r["ok"] for r in health["recent"]] == [False, False, False]
