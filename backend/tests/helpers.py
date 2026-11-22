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


def jpeg_with_gps(shade: int = 0) -> bytes:
    """A camera photo with a GPS position; ``shade`` makes distinct files."""
    img = Image.new("RGB", (64, 48), (200, 30 + shade * 20, 30))
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


def multipart_files(request: httpx.Request) -> list[tuple[str, str, str, bytes]]:
    """(field, filename, content type, bytes) of a multipart request."""
    from email.parser import BytesParser
    from email.policy import default

    request.read()
    head = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=default).parsebytes(head + request.content)
    return [
        (
            part.get_param("name", header="content-disposition") or "",
            part.get_filename() or "",
            part.get_content_type(),
            part.get_payload(decode=True) or b"",
        )
        for part in message.iter_parts()
    ]


def mp4_with_location() -> bytes:
    """A real one-second video carrying a GPS position and a device name."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = f"{tmp}/clip.mp4"
        subprocess.run(  # noqa: S603 - fixed arguments, test only
            [  # noqa: S607
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=red:s=64x48:d=1",
                "-c:v",
                "mpeg4",
                "-metadata",
                "location=+37.9500+058.3800/",
                "-metadata",
                "make=SecretCam",
                "-movflags",
                "+use_metadata_tags",
                out,
            ],
            check=True,
        )
        with open(out, "rb") as fh:
            return fh.read()


def video_tags(data: bytes) -> dict[str, str]:
    import json as _json
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".mp4") as fh:
        fh.write(data)
        fh.flush()
        probe = subprocess.run(  # noqa: S603 - fixed arguments, test only
            [  # noqa: S607
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-of",
                "json",
                fh.name,
            ],
            check=True,
            capture_output=True,
        )
    return {k.lower(): v for k, v in _json.loads(probe.stdout)["format"].get("tags", {}).items()}
