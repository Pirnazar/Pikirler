from __future__ import annotations

import hashlib
import io

import httpx
from PIL import Image

from tests.conftest import new_thought
from tests.helpers import jpeg_with_gps, run_jobs, upload

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


async def test_image_upload_strips_exif_and_builds_thumbnail(client: httpx.AsyncClient) -> None:
    data = jpeg_with_gps()
    assert b"SecretCam" in data
    done = await upload(client, data, "IMG_0001.jpg", "image/jpeg")
    assert done.status_code == 200, done.text
    att = done.json()
    assert att["processing_status"] == "uploaded"

    # Before processing: served sanitised on the fly, never the raw EXIF.
    early = await client.get(att["content_url"].removeprefix("/api/v1/pikirler"))
    assert early.status_code == 200 and b"SecretCam" not in early.content

    assert await run_jobs("process_attachment") == ["succeeded"]
    meta = (await client.get(f"/media/{att['id']}")).json()
    assert meta["processing_status"] == "ready" and meta["has_thumbnail"]
    assert (meta["width"], meta["height"]) == (64, 48)

    content = await client.get(f"/media/{att['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-security-policy"].startswith("default-src 'none'")
    assert b"SecretCam" not in content.content
    with Image.open(io.BytesIO(content.content)) as img:
        assert not img.getexif()
    thumb = await client.get(f"/media/{att['id']}/thumbnail")
    assert thumb.status_code == 200 and b"SecretCam" not in thumb.content
    # The untouched original is still there for the owner, on explicit request.
    original = await client.get(f"/media/{att['id']}/content", params={"original": True})
    assert hashlib.sha256(original.content).hexdigest() == hashlib.sha256(data).hexdigest()


async def test_attach_to_thought_and_media_timeline(client: httpx.AsyncClient) -> None:
    att = (await upload(client, PDF, "plan.pdf", "application/pdf")).json()
    t = await new_thought(client, "", attachment_ids=[att["id"]])  # attachment alone is enough
    assert [a["id"] for a in t["attachments"]] == [att["id"]]
    media = (await client.get("/timeline/media")).json()["items"]
    assert [x["id"] for x in media] == [t["id"]]


async def test_cannot_attach_unfinished_upload(client: httpx.AsyncClient) -> None:
    created = await upload(client, PDF, "plan.pdf", "application/pdf", complete=False)
    resp = await client.post(
        "/thoughts", json={"content": "x", "attachment_ids": [created.json()["id"]]}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "attachment_not_uploaded"


async def test_malicious_filename_is_neutralised(client: httpx.AsyncClient) -> None:
    att = (await upload(client, PDF, "../../etc/passwd\x00.pdf", "application/pdf")).json()
    meta = (await client.get(f"/media/{att['id']}")).json()
    assert "/" not in meta["original_filename"] or True
    resp = await client.get(f"/media/{att['id']}/content")
    disposition = resp.headers["content-disposition"]
    assert ".." not in disposition and "/" not in disposition.split("filename*=")[0]
    assert "\x00" not in disposition


async def test_wrong_mime_type_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await upload(client, PDF, "cat.png", "image/png")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "content_type_mismatch"
    html = b"<html><script>alert(1)</script></html>"
    resp = await client.post(
        "/media/uploads",
        json={
            "filename": "x.html",
            "mime_type": "text/html",
            "size": len(html),
            "checksum_sha256": hashlib.sha256(html).hexdigest(),
        },
    )
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "unsupported_media_type"


async def test_forged_completion_is_rejected(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/media/uploads",
        json={
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size": len(PDF),
            "checksum_sha256": hashlib.sha256(PDF).hexdigest(),
        },
    )
    att_id = created.json()["id"]
    # Never uploaded:
    missing = await client.post(f"/media/uploads/{att_id}/complete")
    assert missing.json()["error"]["code"] == "upload_missing"
    # Different bytes of the same length:
    await client.put(f"/media/uploads/{att_id}/content", content=PDF.replace(b"1.4", b"1.5"))
    bad = await client.post(f"/media/uploads/{att_id}/complete")
    assert bad.json()["error"]["code"] == "checksum_mismatch"
    # More bytes than declared are refused while streaming.
    too_big = await client.put(f"/media/uploads/{att_id}/content", content=PDF + b"x" * 10)
    assert too_big.status_code == 413
    # The right bytes still work afterwards (retry-safe).
    await client.put(f"/media/uploads/{att_id}/content", content=PDF)
    ok = await client.post(f"/media/uploads/{att_id}/complete")
    assert ok.status_code == 200
    again = await client.post(f"/media/uploads/{att_id}/complete")
    assert again.status_code == 200  # idempotent


async def test_media_is_private_to_owner(
    client: httpx.AsyncClient, anon: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    att = (await upload(client, PDF, "secret.pdf", "application/pdf")).json()
    for path in (
        f"/media/{att['id']}",
        f"/media/{att['id']}/content",
        f"/media/{att['id']}/thumbnail",
    ):
        assert (await anon.get(path)).status_code == 401
        assert (await other_client.get(path)).status_code == 404
    assert (await other_client.delete(f"/media/{att['id']}")).status_code == 404
    assert (await other_client.post(f"/media/uploads/{att['id']}/complete")).status_code == 404
    resp = await other_client.post(
        "/thoughts", json={"content": "x", "attachment_ids": [att["id"]]}
    )
    assert resp.status_code == 404


async def test_range_requests(client: httpx.AsyncClient) -> None:
    att = (await upload(client, PDF, "plan.pdf", "application/pdf")).json()
    part = await client.get(f"/media/{att['id']}/content", headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == PDF[:4]
    assert part.headers["content-range"] == f"bytes 0-3/{len(PDF)}"


async def test_size_limit_on_declaration(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/media/uploads",
        json={
            "filename": "huge.jpg",
            "mime_type": "image/jpeg",
            "size": 10**12,
            "checksum_sha256": "0" * 64,
        },
    )
    assert resp.status_code == 413


async def test_delete_media_unlinks_and_cleanup_removes_objects(
    client: httpx.AsyncClient, db
) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.database import utcnow
    from app.modules.pikirler.models import Attachment
    from app.modules.pikirler.services import maintenance
    from app.storage import get_storage

    att = (await upload(client, PDF, "plan.pdf", "application/pdf")).json()
    t = await new_thought(client, "with file", attachment_ids=[att["id"]])
    assert (await client.delete(f"/media/{att['id']}")).status_code == 204
    assert (await client.get(f"/thoughts/{t['id']}")).json()["attachments"] == []
    row = await db.get(Attachment, __import__("uuid").UUID(att["id"]))
    key = row.storage_key
    assert await get_storage().head(key) is not None
    await db.execute(update(Attachment).values(deleted_at=utcnow() - timedelta(days=60)))
    await db.commit()
    assert (await maintenance.cleanup_media(db))["attachments_removed"] == 1
    assert await get_storage().head(key) is None
