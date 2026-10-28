from __future__ import annotations

from app.core.config import get_settings
from app.storage.base import ObjectStorage

_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        settings = get_settings()
        if settings.storage_backend == "local":
            from app.storage.local import LocalStorage

            _storage = LocalStorage(settings.local_storage_path)
        else:
            from app.storage.s3 import S3Storage

            _storage = S3Storage(settings)
    return _storage


def set_storage(storage: ObjectStorage | None) -> None:
    global _storage
    _storage = storage
