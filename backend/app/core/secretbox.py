"""Symmetric encryption for third-party credentials kept on behalf of the owner
(e.g. a Teswirler session). The key is derived from ``PIKIRLER_SECRET_KEY``;
rotating that secret makes stored values unreadable, so callers must treat
``SecretUnreadable`` as "reconnect the account"."""

from __future__ import annotations

import base64
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings


class SecretUnreadable(Exception):
    """The ciphertext was made with another key or was tampered with."""


@lru_cache(maxsize=4)
def _fernet(secret: str, purpose: str) -> Fernet:
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=f"pikirler/{purpose}".encode()
    ).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def seal(value: dict[str, Any], purpose: str) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return _fernet(get_settings().secret_key, purpose).encrypt(raw).decode()


def unseal(token: str, purpose: str) -> dict[str, Any]:
    try:
        raw = _fernet(get_settings().secret_key, purpose).decrypt(token.encode())
    except InvalidToken as exc:
        raise SecretUnreadable(purpose) from exc
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SecretUnreadable(purpose)
    return value
