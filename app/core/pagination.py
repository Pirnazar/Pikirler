"""Opaque keyset cursors over (created_at DESC, id DESC)."""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.core.exceptions import ValidationFailed


@dataclass(frozen=True)
class Cursor:
    created_at: datetime
    id: uuid.UUID

    def encode(self) -> str:
        raw = json.dumps({"c": self.created_at.isoformat(), "i": str(self.id)}).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @classmethod
    def decode(cls, value: str) -> Cursor:
        try:
            padded = value + "=" * (-len(value) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded.encode()))
            created = datetime.fromisoformat(data["c"])
            if created.tzinfo is None:
                raise ValueError("naive timestamp")
            return cls(created_at=created, id=uuid.UUID(data["i"]))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationFailed("Invalid cursor", code="invalid_cursor") from exc
