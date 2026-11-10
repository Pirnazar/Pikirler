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

PUBLIC = {
    "news.example.org": "93.184.216.34",
    "cdn.example.org": "93.184.216.35",
    "api.teswirler.com": "104.21.1.1",
    "teswirler.com": "104.21.1.2",
    "sneaky.example.org": "10.0.0.5",
    "moved.example.org": "93.184.216.36",
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
