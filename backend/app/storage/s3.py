"""S3-compatible storage (RustFS in the compose stacks). boto3 runs in worker threads."""

from __future__ import annotations

import asyncio
import os
import tarfile
import tempfile
from collections.abc import AsyncIterator
from typing import IO, Any
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.storage.base import ObjectInfo, TooLarge, validate_key

_SPOOL_IN_MEMORY = 8 * 1024 * 1024
_PAX_TYPE = "PIKIRLER.content_type"


class S3Storage:
    name = "s3"

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        common: dict[str, Any] = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
            "region_name": settings.s3_region,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 4, "mode": "standard"},
            ),
        }
        self._client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url, **common)
        # Presigned URLs are signed for the host the browser will talk to.
        self._public = (
            boto3.client("s3", endpoint_url=settings.s3_public_endpoint_url, **common)
            if settings.s3_public_endpoint_url
            else None
        )

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str, max_bytes: int
    ) -> int:
        validate_key(key)
        total = 0
        # Spool to disk past 8 MiB; upload_fileobj then does a multipart upload.
        with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
            async for chunk in stream:
                total += len(chunk)
                if total > max_bytes:
                    raise TooLarge
                spool.write(chunk)
            spool.seek(0)
            await asyncio.to_thread(
                self._client.upload_fileobj,
                spool,
                self.bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        return total

    async def put_file(self, key: str, fileobj: IO[bytes], content_type: str) -> None:
        validate_key(key)
        await asyncio.to_thread(
            self._client.upload_fileobj,
            fileobj,
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )

    async def head(self, key: str) -> ObjectInfo | None:
        validate_key(key)
        try:
            resp = await asyncio.to_thread(self._client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return ObjectInfo(size=int(resp["ContentLength"]), content_type=resp.get("ContentType"))

    async def open_stream(
        self, key: str, chunk_size: int = 1024 * 1024, start: int = 0, end: int | None = None
    ) -> AsyncIterator[bytes]:
        validate_key(key)
        extra: dict[str, Any] = {}
        if start or end is not None:
            extra["Range"] = f"bytes={start}-{'' if end is None else end}"
        resp = await asyncio.to_thread(
            self._client.get_object, Bucket=self.bucket, Key=key, **extra
        )
        body = resp["Body"]
        try:
            while chunk := await asyncio.to_thread(body.read, chunk_size):
                yield chunk
        finally:
            body.close()

    async def read_prefix(self, key: str, length: int) -> bytes:
        validate_key(key)
        resp = await asyncio.to_thread(
            self._client.get_object, Bucket=self.bucket, Key=key, Range=f"bytes=0-{length - 1}"
        )
        data: bytes = await asyncio.to_thread(resp["Body"].read)
        return data

    async def download_to(self, key: str, path: str) -> None:
        validate_key(key)
        await asyncio.to_thread(self._client.download_file, self.bucket, key, path)

    async def delete(self, key: str) -> None:
        validate_key(key)
        await asyncio.to_thread(self._client.delete_object, Bucket=self.bucket, Key=key)

    async def presigned_get(
        self, key: str, filename: str, content_type: str, ttl_seconds: int
    ) -> str | None:
        if self._public is None:
            return None
        validate_key(key)
        disposition = f"inline; filename*=UTF-8''{quote(filename)}"
        url: str = await asyncio.to_thread(
            self._public.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=ttl_seconds,
        )
        return url

    async def presigned_put(self, key: str, content_type: str, ttl_seconds: int) -> str | None:
        if self._public is None:
            return None
        validate_key(key)
        url: str = await asyncio.to_thread(
            self._public.generate_presigned_url,
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=ttl_seconds,
        )
        return url

    async def ping(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)

    async def ensure_bucket(self) -> bool:
        """Create the bucket if it is missing; True when it was created. A new
        bucket has no policy, so it is private: nothing is readable anonymously."""
        try:
            await self.ping()
            return False
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"404", "NoSuchBucket", "NotFound"}:
                raise
        await asyncio.to_thread(self._client.create_bucket, Bucket=self.bucket)
        return True

    # Backups: the whole bucket as one tar stream, content types kept in PAX headers.
    def export_tar(self, out: IO[bytes]) -> int:
        count = 0
        with tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT) as tar:
            pages = self._client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
            for page in pages:
                for obj in page.get("Contents", []):
                    resp = self._client.get_object(Bucket=self.bucket, Key=obj["Key"])
                    info = tarfile.TarInfo(obj["Key"])
                    info.size = int(obj["Size"])
                    info.mtime = int(obj["LastModified"].timestamp())
                    info.pax_headers = {_PAX_TYPE: resp.get("ContentType") or ""}
                    tar.addfile(info, resp["Body"])
                    count += 1
        return count

    def import_tar(self, src: IO[bytes]) -> int:
        count = 0
        with tarfile.open(fileobj=src, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                key = validate_key(member.name.removeprefix("./"))
                body = tar.extractfile(member)
                assert body is not None
                extra = {"ContentType": ct} if (ct := member.pax_headers.get(_PAX_TYPE)) else {}
                # A streamed tar member is not seekable, which boto3 requires.
                with tempfile.SpooledTemporaryFile(max_size=_SPOOL_IN_MEMORY) as spool:
                    while chunk := body.read(1024 * 1024):
                        spool.write(chunk)
                    spool.seek(0)
                    self._client.upload_fileobj(spool, self.bucket, key, ExtraArgs=extra)
                count += 1
        return count


def tmp_path(suffix: str = "") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path
