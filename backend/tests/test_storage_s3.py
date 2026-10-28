"""The S3 backend against the real RustFS of the compose stack.
Skipped when PIKIRLER_TEST_S3_ENDPOINT is not set."""

from __future__ import annotations

import asyncio
import io
import os
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.storage.base import InvalidStorageKey, TooLarge

ENDPOINT = os.environ.get("PIKIRLER_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not ENDPOINT, reason="no S3 endpoint configured")


def _storage(bucket: str):  # type: ignore[no-untyped-def]
    from app.storage.s3 import S3Storage

    settings = get_settings().model_copy(
        update={
            "s3_endpoint_url": ENDPOINT,
            "s3_public_endpoint_url": ENDPOINT,
            "s3_access_key": os.environ["PIKIRLER_S3_ACCESS_KEY"],
            "s3_secret_key": os.environ["PIKIRLER_S3_SECRET_KEY"],
            "s3_bucket": bucket,
        }
    )
    return S3Storage(settings)


@pytest.fixture
async def s3():  # type: ignore[no-untyped-def]
    storage = _storage(os.environ.get("PIKIRLER_S3_BUCKET", "pikirler-private"))
    await storage.ensure_bucket()
    return storage


async def test_ensure_bucket_creates_once() -> None:
    storage = _storage(f"pikirler-test-{uuid.uuid4().hex[:12]}")
    assert await storage.ensure_bucket() is True
    assert await storage.ensure_bucket() is False
    await storage.ping()
    await asyncio.to_thread(storage._client.delete_bucket, Bucket=storage.bucket)


async def test_bucket_tar_roundtrip_keeps_bytes_and_types() -> None:
    src = _storage(f"pikirler-test-{uuid.uuid4().hex[:12]}")
    dst = _storage(f"pikirler-test-{uuid.uuid4().hex[:12]}")
    await src.ensure_bucket()
    await dst.ensure_bucket()
    files = {
        _key(): (b"\x89PNG fake image", "image/png"),
        _key("thumbnail"): (b"x" * 3000, "image/webp"),
    }
    for key, (data, ctype) in files.items():
        await src.put_file(key, io.BytesIO(data), ctype)
    buf = io.BytesIO()
    assert await asyncio.to_thread(src.export_tar, buf) == 2
    buf.seek(0)
    assert await asyncio.to_thread(dst.import_tar, buf) == 2
    for key, (data, ctype) in files.items():
        info = await dst.head(key)
        assert info is not None and info.content_type == ctype
        assert b"".join([c async for c in dst.open_stream(key)]) == data
    for storage in (src, dst):
        for key in files:
            await storage.delete(key)
        await asyncio.to_thread(storage._client.delete_bucket, Bucket=storage.bucket)


def _key(variant: str = "original") -> str:
    return f"owners/{uuid.uuid4()}/attachments/{uuid.uuid4()}/{variant}"


async def _chunks(data: bytes):  # type: ignore[no-untyped-def]
    for i in range(0, len(data), 7):
        yield data[i : i + 7]


async def test_put_head_range_delete(s3) -> None:  # type: ignore[no-untyped-def]
    await s3.ping()
    key = _key()
    data = b"pikirler private bytes " * 10
    assert await s3.put_stream(key, _chunks(data), "application/pdf", len(data)) == len(data)
    info = await s3.head(key)
    assert info is not None and info.size == len(data)
    assert b"".join([c async for c in s3.open_stream(key, start=2, end=9)]) == data[2:10]
    assert await s3.read_prefix(key, 8) == data[:8]
    await s3.delete(key)
    assert await s3.head(key) is None


async def test_limits_and_key_validation(s3) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(TooLarge):
        await s3.put_stream(_key(), _chunks(b"x" * 50), "text/plain", 10)
    with pytest.raises(InvalidStorageKey):
        await s3.put_file("owners/../../etc/passwd", io.BytesIO(b"x"), "text/plain")


async def test_bucket_is_private_and_presigned_urls_expire(s3) -> None:  # type: ignore[no-untyped-def]
    key = _key()
    await s3.put_file(key, io.BytesIO(b"secret"), "text/plain")
    async with httpx.AsyncClient() as client:
        anonymous = await client.get(f"{ENDPOINT}/{s3.bucket}/{key}")
        assert anonymous.status_code == 403
        url = await s3.presigned_get(key, "a.txt", "text/plain", 60)
        assert "X-Amz-Expires=60" in url
        assert (await client.get(url)).content == b"secret"
    await s3.delete(key)
