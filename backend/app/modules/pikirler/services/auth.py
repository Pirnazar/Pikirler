"""Owner authentication: sessions, Magic Word, service clients."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import Forbidden, Locked, NotFound, RateLimited, Unauthorized
from app.core.security import (
    SERVICE_TOKEN_PREFIX,
    constant_time_equals,
    hash_secret,
    needs_rehash,
    new_token,
    normalize_magic_word,
    token_digest,
    verify_secret,
)
from app.modules.pikirler.models import AuthSession, Owner, ServiceClient
from app.modules.pikirler.models.enums import ThoughtSource

# Scopes a service client may hold. Browser sessions hold all of them plus the
# account-level ones (sessions, settings, export, purge) that services never get.
SERVICE_SCOPES = frozenset(
    {
        "thoughts:read",
        "thoughts:write",
        "search",
        "media:read",
        "media:write",
        "voice:write",
        "memories:read",
        "ai",
    }
)
OWNER_ONLY_SCOPE = "account"


@dataclass(frozen=True)
class Principal:
    owner_id: uuid.UUID
    kind: str  # "session" | "service"
    scopes: frozenset[str]
    source: str
    session_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None
    magic_word_enabled: bool = False
    unlocked: bool = True
    timezone: str = "UTC"
    ai_external_enabled: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    def has(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True)
class IssuedSession:
    session: AuthSession
    token: str
    csrf_token: str


def _email_bucket(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


async def login(
    db: AsyncSession, email: str, password: str, ip: str | None, user_agent: str | None
) -> tuple[Owner, IssuedSession]:
    settings = get_settings()
    window = settings.login_rate_window_seconds
    await ratelimit.hit(f"login:ip:{ip or 'unknown'}", settings.login_rate_limit_per_ip, window)
    await ratelimit.hit(
        f"login:acct:{_email_bucket(email)}", settings.login_rate_limit_per_account, window
    )

    owner = (
        await db.execute(select(Owner).where(Owner.email.ilike(email.strip())).with_for_update())
    ).scalar_one_or_none()
    now = utcnow()
    if owner is not None and owner.locked_until and owner.locked_until > now:
        verify_secret(None, password)  # same timing as a normal attempt
        raise RateLimited("Too many failed attempts, try again later", code="account_locked")

    if owner is None or not verify_secret(owner.password_hash, password):
        if owner is not None:
            owner.failed_login_count += 1
            if owner.failed_login_count >= settings.lockout_threshold:
                owner.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                owner.failed_login_count = 0
            await db.commit()
        raise Unauthorized("Invalid email or password", code="invalid_credentials")

    owner.failed_login_count = 0
    owner.locked_until = None
    if needs_rehash(owner.password_hash):
        owner.password_hash = hash_secret(password)
    issued = await _issue_session(db, owner, ip, user_agent)
    await db.commit()
    await ratelimit.reset(f"login:acct:{_email_bucket(email)}")
    return owner, issued


async def _issue_session(
    db: AsyncSession, owner: Owner, ip: str | None, user_agent: str | None
) -> IssuedSession:
    settings = get_settings()
    token, csrf = new_token(), new_token()
    now = utcnow()
    session = AuthSession(
        owner_id=owner.id,
        token_hash=token_digest(token),
        csrf_token_hash=token_digest(csrf),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
        ip_address=ip,
        user_agent=(user_agent or "")[:512] or None,
        # Without a Magic Word the space is unlocked for the whole session.
        unlocked_until=None
        if owner.magic_word_hash
        else now + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    await db.flush()
    return IssuedSession(session=session, token=token, csrf_token=csrf)


async def authenticate_session(db: AsyncSession, token: str) -> tuple[AuthSession, Owner]:
    settings = get_settings()
    row = (
        await db.execute(
            select(AuthSession, Owner)
            .join(Owner, Owner.id == AuthSession.owner_id)
            .where(AuthSession.token_hash == token_digest(token))
        )
    ).one_or_none()
    if row is None:
        raise Unauthorized("Not authenticated")
    session, owner = row
    now = utcnow()
    idle_limit = session.last_seen_at + timedelta(hours=settings.session_idle_timeout_hours)
    if session.revoked_at is not None or session.expires_at <= now or idle_limit <= now:
        raise Unauthorized("Session expired or revoked", code="session_expired")
    if now - session.last_seen_at > timedelta(minutes=5):
        await db.execute(
            update(AuthSession).where(AuthSession.id == session.id).values(last_seen_at=now)
        )
        await db.commit()
    return session, owner


def check_csrf(session: AuthSession, header_value: str | None) -> None:
    if not header_value or not constant_time_equals(
        token_digest(header_value), session.csrf_token_hash
    ):
        raise Forbidden("CSRF token missing or invalid", code="csrf_failed")


def session_principal(session: AuthSession, owner: Owner) -> Principal:
    now = utcnow()
    magic = owner.magic_word_hash is not None
    unlocked = session.unlocked_until is not None and session.unlocked_until > now
    return Principal(
        owner_id=owner.id,
        kind="session",
        scopes=SERVICE_SCOPES | {OWNER_ONLY_SCOPE},
        source=ThoughtSource.web.value,
        session_id=session.id,
        magic_word_enabled=magic,
        unlocked=unlocked or not magic,
        timezone=owner.timezone,
        ai_external_enabled=owner.ai_external_enabled,
    )


async def authenticate_service(db: AsyncSession, token: str) -> Principal:
    if not token.startswith(SERVICE_TOKEN_PREFIX):
        raise Unauthorized("Invalid service token")
    row = (
        await db.execute(
            select(ServiceClient, Owner)
            .join(Owner, Owner.id == ServiceClient.owner_id)
            .where(
                ServiceClient.token_hash == token_digest(token), ServiceClient.revoked_at.is_(None)
            )
        )
    ).one_or_none()
    if row is None:
        raise Unauthorized("Invalid service token")
    client, owner = row
    now = utcnow()
    if client.last_used_at is None or now - client.last_used_at > timedelta(minutes=5):
        await db.execute(
            update(ServiceClient).where(ServiceClient.id == client.id).values(last_used_at=now)
        )
        await db.commit()
    return Principal(
        owner_id=owner.id,
        kind="service",
        scopes=frozenset(client.scopes) & SERVICE_SCOPES,
        source=client.source,
        client_id=client.id,
        timezone=owner.timezone,
        ai_external_enabled=owner.ai_external_enabled,
        extra={"client": client.name},
    )


def require_unlocked(principal: Principal) -> None:
    if principal.kind == "session" and not principal.unlocked:
        raise Locked("Enter the Magic Word to unlock Pikirler")


async def unlock(db: AsyncSession, principal: Principal, magic_word: str) -> datetime:
    settings = get_settings()
    assert principal.session_id is not None
    await ratelimit.hit(
        f"unlock:{principal.owner_id}",
        settings.unlock_rate_limit,
        settings.login_rate_window_seconds,
    )
    owner = await db.get(Owner, principal.owner_id)
    if owner is None or owner.magic_word_hash is None:
        raise NotFound("Magic Word is not configured", code="magic_word_not_configured")
    if not verify_secret(owner.magic_word_hash, normalize_magic_word(magic_word)):
        raise Unauthorized("Wrong Magic Word", code="invalid_magic_word")
    until = utcnow() + timedelta(minutes=settings.magic_word_unlock_minutes)
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == principal.session_id)
        .values(unlocked_until=until)
    )
    await db.commit()
    await ratelimit.reset(f"unlock:{principal.owner_id}")
    return until


async def lock(db: AsyncSession, principal: Principal) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == principal.session_id)
        .values(unlocked_until=None)
    )
    await db.commit()


async def verify_password(db: AsyncSession, owner_id: uuid.UUID, password: str) -> Owner:
    settings = get_settings()
    await ratelimit.hit(
        f"reauth:{owner_id}",
        settings.login_rate_limit_per_account,
        settings.login_rate_window_seconds,
    )
    owner = await db.get(Owner, owner_id)
    if owner is None or not verify_secret(owner.password_hash, password):
        raise Unauthorized("Wrong password", code="invalid_credentials")
    return owner


async def set_magic_word(
    db: AsyncSession, principal: Principal, password: str, magic_word: str | None
) -> None:
    owner = await verify_password(db, principal.owner_id, password)
    owner.magic_word_hash = hash_secret(normalize_magic_word(magic_word)) if magic_word else None
    now = utcnow()
    # Enabling the word keeps only the current session unlocked.
    await db.execute(
        update(AuthSession)
        .where(AuthSession.owner_id == owner.id, AuthSession.id != principal.session_id)
        .values(unlocked_until=None if magic_word else now + timedelta(days=365))
    )
    await db.commit()


async def change_password(db: AsyncSession, principal: Principal, current: str, new: str) -> None:
    owner = await verify_password(db, principal.owner_id, current)
    now = utcnow()
    owner.password_hash = hash_secret(new)
    owner.password_changed_at = now
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.owner_id == owner.id,
            AuthSession.id != principal.session_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )  # every other session is revoked; the current one stays signed in
    await db.commit()


async def list_sessions(db: AsyncSession, owner_id: uuid.UUID) -> list[AuthSession]:
    now = utcnow()
    rows = await db.execute(
        select(AuthSession)
        .where(
            AuthSession.owner_id == owner_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .order_by(AuthSession.last_seen_at.desc())
    )
    return list(rows.scalars())


async def revoke_session(db: AsyncSession, owner_id: uuid.UUID, session_id: uuid.UUID) -> None:
    result = await db.execute(
        update(AuthSession)
        .where(
            AuthSession.id == session_id,
            AuthSession.owner_id == owner_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise NotFound("Session not found")
    await db.commit()


async def revoke_all_sessions(db: AsyncSession, owner_id: uuid.UUID) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.owner_id == owner_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


# --- provisioning (CLI only; there is no registration endpoint) -------------


async def create_owner(
    db: AsyncSession, email: str, password: str, display_name: str | None, timezone: str
) -> Owner:
    owner = Owner(
        email=email.strip().lower(),
        password_hash=hash_secret(password),
        display_name=display_name,
        timezone=timezone,
    )
    db.add(owner)
    await db.commit()
    return owner


async def create_service_client(
    db: AsyncSession, owner_id: uuid.UUID, name: str, scopes: list[str], source: str
) -> tuple[ServiceClient, str]:
    unknown = set(scopes) - SERVICE_SCOPES
    if unknown:
        raise ValueError(f"unknown scopes: {sorted(unknown)}")
    ThoughtSource(source)
    token = SERVICE_TOKEN_PREFIX + new_token()
    client = ServiceClient(
        owner_id=owner_id,
        name=name,
        token_hash=token_digest(token),
        scopes=sorted(set(scopes)),
        source=source,
    )
    db.add(client)
    await db.commit()
    return client, token
