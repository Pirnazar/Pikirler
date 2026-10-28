"""Request-scoped dependencies: database session and the authenticated principal."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_database
from app.core.exceptions import Forbidden, Unauthorized
from app.modules.pikirler.services import auth as auth_service
from app.modules.pikirler.services import jobs
from app.modules.pikirler.services.auth import Principal

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_database().sessionmaker() as session:
        yield session
        # Jobs written by the request's committed transaction go to the queue now;
        # if this fails the dispatcher picks them up from the jobs table.
        await jobs.kick(session)


DB = Annotated[AsyncSession, Depends(get_db)]


async def authenticate(request: Request, db: DB) -> Principal:
    settings = get_settings()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        principal = await auth_service.authenticate_service(db, authorization[7:].strip())
    else:
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            raise Unauthorized("Not authenticated")
        session, owner = await auth_service.authenticate_session(db, token)
        if request.method not in SAFE_METHODS:
            auth_service.check_csrf(session, request.headers.get(settings.csrf_header_name))
        principal = auth_service.session_principal(session, owner)
    request.state.owner_id = principal.owner_id
    return principal


def require(scope: str, *, allow_locked: bool = False) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: authenticated, holding ``scope``, Magic Word unlocked."""

    async def dependency(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if not principal.has(scope):
            raise Forbidden(f"Missing scope: {scope}", code="insufficient_scope")
        if not allow_locked:
            auth_service.require_unlocked(principal)
        return principal

    return dependency


ReadThoughts = Annotated[Principal, Depends(require("thoughts:read"))]
WriteThoughts = Annotated[Principal, Depends(require("thoughts:write"))]
SearchAccess = Annotated[Principal, Depends(require("search"))]
MediaRead = Annotated[Principal, Depends(require("media:read"))]
MediaWrite = Annotated[Principal, Depends(require("media:write"))]
VoiceWrite = Annotated[Principal, Depends(require("voice:write"))]
MemoriesRead = Annotated[Principal, Depends(require("memories:read"))]
AIAccess = Annotated[Principal, Depends(require("ai"))]
Account = Annotated[Principal, Depends(require("account"))]
AccountLocked = Annotated[Principal, Depends(require("account", allow_locked=True))]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None
