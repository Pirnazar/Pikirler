"""Filesystem storage for single-host setups and tests."""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO

from app.storage.base import ObjectInfo, TooLarge, validate_key


class LocalStorage:
    name = "local"

    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / validate_key(key)).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes the storage root")
        return path

    async def put_stream(
        self, key: str, stream: AsyncIterator[bytes], content_type: str, max_bytes: int
    ) -> int:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        total = 0
        fh = await asyncio.to_thread(open, tmp, "wb")
        try:
            async for chunk in stream:
                total += len(chunk)
                if total > max_bytes:
                    raise TooLarge
                await asyncio.to_thread(fh.write, chunk)
        except BaseException:
            fh.close()
            tmp.unlink(missing_ok=True)
            raise
        fh.close()
        await asyncio.to_thread(os.replace, tmp, path)
        return total

    async def put_file(self, key: str, fileobj: IO[bytes], content_type: str) -> None:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as out:
                shutil.copyfileobj(fileobj, out)

        await asyncio.to_thread(_write)

    async def head(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.is_file():
            return None
        return ObjectInfo(size=path.stat().st_size, content_type=None)

    async def open_stream(
        self, key: str, chunk_size: int = 1024 * 1024, start: int = 0, end: int | None = None
    ) -> AsyncIterator[bytes]:
        path = self._path(key)
        fh = await asyncio.to_thread(open, path, "rb")
        try:
            await asyncio.to_thread(fh.seek, start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                size = chunk_size if remaining is None else min(chunk_size, remaining)
                chunk = await asyncio.to_thread(fh.read, size)
                if not chunk:
                    break
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
        finally:
            fh.close()

    async def read_prefix(self, key: str, length: int) -> bytes:
        def _read() -> bytes:
            with open(self._path(key), "rb") as fh:
                return fh.read(length)

        return await asyncio.to_thread(_read)

    async def download_to(self, key: str, path: str) -> None:
        await asyncio.to_thread(shutil.copyfile, self._path(key), path)

    async def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    async def presigned_get(
        self, key: str, filename: str, content_type: str, ttl_seconds: int
    ) -> str | None:
        return None

    async def presigned_put(self, key: str, content_type: str, ttl_seconds: int) -> str | None:
        return None

    async def ping(self) -> None:
        if not self.root.is_dir():
            raise RuntimeError("storage root missing")
