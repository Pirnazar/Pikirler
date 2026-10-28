from __future__ import annotations

import hashlib
import io
import uuid
import wave

import httpx
from PIL import Image
from sqlalchemy import select

from app.core.database import get_database
from app.modules.pikirler.models import Job
from app.modules.pikirler.tasks import runner


def jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (64, 48), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    gps = {1: "N", 2: (37.0, 57.0, 0.0), 3: "E", 4: (58.0, 23.0, 0.0)}
    exif[0x8825] = gps  # GPSInfo
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def wav_bytes(seconds: float = 0.5) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


async def upload(
    client: httpx.AsyncClient, data: bytes, filename: str, mime: str, complete: bool = True
) -> httpx.Response:
    created = await client.post(
        "/media/uploads",
        json={
            "filename": filename,
            "mime_type": mime,
            "size": len(data),
            "checksum_sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert created.status_code == 201, created.text
    att_id = created.json()["id"]
    put = await client.put(f"/media/uploads/{att_id}/content", content=data)
    assert put.status_code == 200, put.text
    if not complete:
        return created
    return await client.post(f"/media/uploads/{att_id}/complete")


async def run_jobs(kind: str | None = None, rounds: int = 5) -> list[str]:
    """Run every due job once per round, like the worker would."""
    statuses: list[str] = []
    for _ in range(rounds):
        async with get_database().sessionmaker() as db:
            stmt = select(Job.id).where(Job.status.in_(["pending", "failed"]))
            if kind:
                stmt = stmt.where(Job.kind == kind)
            ids: list[uuid.UUID] = list((await db.execute(stmt)).scalars())
        if not ids:
            break
        for job_id in ids:
            statuses.append(await runner.execute(job_id))
    return statuses
