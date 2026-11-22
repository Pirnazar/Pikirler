"""Errors shared by the sites a thought can be published on (Teswirler, makalam).

Each site subclasses these with its own stable ``code`` (``<site>_<kind>``),
which is stored on the cross-post and shown to the owner. ``message`` is the
site's own explanation and never contains our request body.
"""

from __future__ import annotations

from typing import Any


class RemoteError(Exception):
    code = "remote_error"

    def __init__(self, message: str = "", *, status: int | None = None) -> None:
        super().__init__(message or self.code)
        self.message = message[:300]
        self.status = status
        # The site session as it was when the error happened (it may have been
        # renewed before the failure), so it can still be kept.
        self.session: dict[str, Any] | None = None


class LoginRejected(RemoteError):
    """Wrong login or password."""


class SessionExpired(RemoteError):
    """The stored session no longer works; the owner must connect again."""


class Rejected(RemoteError):
    """The site refused the post (validation, limits). Nothing was created."""


class Unavailable(RemoteError):
    """The site could not be reached or answered 5xx/429 before accepting."""


class OutcomeUnknown(RemoteError):
    """The request was sent but no answer came back: the post may exist."""
