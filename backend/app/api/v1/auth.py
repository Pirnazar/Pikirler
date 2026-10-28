"""Owner sign-in. There is deliberately no registration endpoint: the owner is
created with ``pikirler create-owner``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response

from app.api.deps import DB, Account, AccountLocked, client_ip
from app.core.config import get_settings
from app.modules.pikirler.models import Owner
from app.modules.pikirler.schemas.auth import (
    LoginRequest,
    MagicWordUpdate,
    OwnerOut,
    PasswordChange,
    SessionOut,
    SessionState,
    UnlockRequest,
    UnlockResponse,
)
from app.modules.pikirler.schemas.common import Ok
from app.modules.pikirler.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    common = {
        "secure": s.cookie_secure,
        "samesite": s.cookie_samesite,
        "domain": s.cookie_domain,
        "max_age": max_age,
        "path": "/",
    }
    response.set_cookie(s.session_cookie_name, token, httponly=True, **common)  # type: ignore[arg-type]
    response.set_cookie(s.csrf_cookie_name, csrf, httponly=False, **common)  # type: ignore[arg-type]


def _clear_cookies(response: Response) -> None:
    s = get_settings()
    for name in (s.session_cookie_name, s.csrf_cookie_name):
        response.delete_cookie(
            name,
            path="/",
            domain=s.cookie_domain,
            secure=s.cookie_secure,
            samesite=s.cookie_samesite,
        )


@router.post("/login", response_model=SessionState)
async def login(body: LoginRequest, request: Request, response: Response, db: DB) -> SessionState:
    owner, issued = await auth_service.login(
        db, body.email, body.password, client_ip(request), request.headers.get("user-agent")
    )
    _set_cookies(response, issued.token, issued.csrf_token)
    principal = auth_service.session_principal(issued.session, owner)
    return SessionState(
        owner=OwnerOut.model_validate(owner),
        session_id=issued.session.id,
        magic_word_enabled=principal.magic_word_enabled,
        unlocked=principal.unlocked,
        csrf_token=issued.csrf_token,
    )


@router.get("/me", response_model=SessionState)
async def me(principal: AccountLocked, db: DB) -> SessionState:
    owner = await db.get(Owner, principal.owner_id)
    return SessionState(
        owner=OwnerOut.model_validate(owner),
        session_id=principal.session_id,
        magic_word_enabled=principal.magic_word_enabled,
        unlocked=principal.unlocked,
    )


@router.post("/logout", response_model=Ok)
async def logout(principal: AccountLocked, response: Response, db: DB) -> Ok:
    assert principal.session_id is not None
    await auth_service.revoke_session(db, principal.owner_id, principal.session_id)
    _clear_cookies(response)
    return Ok()


@router.post("/logout-all", response_model=Ok)
async def logout_all(principal: AccountLocked, response: Response, db: DB) -> Ok:
    await auth_service.revoke_all_sessions(db, principal.owner_id)
    _clear_cookies(response)
    return Ok()


@router.post("/unlock", response_model=UnlockResponse)
async def unlock(body: UnlockRequest, principal: AccountLocked, db: DB) -> UnlockResponse:
    until = await auth_service.unlock(db, principal, body.magic_word)
    return UnlockResponse(unlocked_until=until)


@router.post("/lock", response_model=Ok)
async def lock(principal: AccountLocked, db: DB) -> Ok:
    await auth_service.lock(db, principal)
    return Ok()


@router.put("/magic-word", response_model=Ok)
async def set_magic_word(body: MagicWordUpdate, principal: Account, db: DB) -> Ok:
    await auth_service.set_magic_word(db, principal, body.password, body.magic_word)
    return Ok()


@router.post("/password", response_model=Ok)
async def change_password(body: PasswordChange, principal: Account, db: DB) -> Ok:
    await auth_service.change_password(db, principal, body.current_password, body.new_password)
    return Ok()


@router.get("/sessions", response_model=list[SessionOut])
async def sessions(principal: Account, db: DB) -> list[SessionOut]:
    rows = await auth_service.list_sessions(db, principal.owner_id)
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            ip_address=str(s.ip_address) if s.ip_address else None,
            user_agent=s.user_agent,
            current=s.id == principal.session_id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}", response_model=Ok)
async def revoke(session_id: uuid.UUID, principal: Account, db: DB) -> Ok:
    await auth_service.revoke_session(db, principal.owner_id, session_id)
    return Ok()
