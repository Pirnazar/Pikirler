"""End-to-end check of a running stack (API + worker + PostgreSQL + Redis + S3).

    docker compose run --rm --no-deps -e SMOKE_PASSWORD=... api \
        python scripts/smoke_test.py http://api:8000 owner@example.com

Creates a few thoughts and one image in the owner's space and deletes them
again at the end (soft delete; files are removed by the cleanup job).
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import time

import httpx
from PIL import Image

PREFIX = "/api/v1/pikirler"


def main(base: str, email: str) -> None:
    password = os.environ["SMOKE_PASSWORD"]
    with httpx.Client(base_url=base + PREFIX, timeout=30) as c:
        ready = c.get("/health/ready").json()
        print("ready:", ready)
        assert ready["status"] == "ok"
        login = c.post("/auth/login", json={"email": email, "password": password})
        login.raise_for_status()
        c.headers["X-CSRF-Token"] = login.json()["csrf_token"]

        t = c.post("/thoughts", json={"content": "Smoke test: Ýyldyrym #smoke 人工智能"}).json()
        r = c.post(f"/thoughts/{t['id']}/replies", json={"content": "Smoke reply"}).json()
        hits = c.get("/search", params={"q": "yyldyrym"}).json()["results"]
        assert t["id"] in [h["thought_id"] for h in hits], hits
        print("thought + reply + search: ok")

        img = Image.new("RGB", (32, 32), (10, 120, 200))
        exif = Image.Exif()
        exif[0x010F] = "SmokeCam"
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        data = buf.getvalue()
        up = c.post("/media/uploads", json={
            "filename": "smoke.jpg", "mime_type": "image/jpeg", "size": len(data),
            "checksum_sha256": hashlib.sha256(data).hexdigest()}).json()
        c.put(f"/media/uploads/{up['id']}/content", content=data).raise_for_status()
        c.post(f"/media/uploads/{up['id']}/complete").raise_for_status()
        for _ in range(30):  # the worker builds the preview and thumbnail
            meta = c.get(f"/media/{up['id']}").json()
            if meta["processing_status"] == "ready":
                break
            time.sleep(1)
        assert meta["processing_status"] == "ready" and meta["has_thumbnail"], meta
        preview = c.get(f"/media/{up['id']}/content").content
        assert b"SmokeCam" not in preview
        print("upload -> S3 -> worker preview/thumbnail, EXIF stripped: ok")

        for tid in (r["id"], t["id"]):
            c.delete(f"/thoughts/{tid}").raise_for_status()
        c.delete(f"/media/{up['id']}").raise_for_status()
        c.post("/auth/logout").raise_for_status()
        print("smoke test passed")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
