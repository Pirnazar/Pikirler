"""Password hashing (Argon2id) and opaque token helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from app.core.config import get_settings

# OWASP-recommended Argon2id parameters (m=64 MiB, t=3, p=4).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4, type=Type.ID)

# Verified against when the account does not exist, so a missing owner and a
# wrong password take the same time.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

SESSION_TOKEN_BYTES = 32
SERVICE_TOKEN_PREFIX = "pks_"  # noqa: S105 -- a prefix, not a secret


def hash_secret(value: str) -> str:
    return _hasher.hash(value)


def verify_secret(stored_hash: str | None, candidate: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, candidate) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def normalize_magic_word(value: str) -> str:
    """Magic words are compared case-insensitively and whitespace-trimmed."""
    import unicodedata

    return unicodedata.normalize("NFKC", value).strip().casefold()


def new_token(nbytes: int = SESSION_TOKEN_BYTES) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    """Keyed digest of a high-entropy token; only the digest is stored."""
    key = get_settings().secret_key.encode()
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def sign_value(value: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()
