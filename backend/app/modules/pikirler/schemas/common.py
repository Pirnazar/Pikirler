from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(Schema, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this envelope."""

    error: ErrorBody
    request_id: str | None = None


class Ok(BaseModel):
    ok: bool = True


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "CSRF failure or missing scope"},
    404: {"model": ErrorResponse, "description": "Not found (or owned by someone else)"},
    422: {"model": ErrorResponse, "description": "Validation failed"},
    423: {"model": ErrorResponse, "description": "Magic Word required"},
}
