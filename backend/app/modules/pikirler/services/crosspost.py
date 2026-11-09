"""Publishing a copy of a thought on another site (Teswirler).

Rules:
* a thought leaves Pikirler only when the owner ticked a target for it, in
  their own browser session (service tokens cannot cross-post);
* the Teswirler password is used once to sign in and is never stored; the
  session it yields is kept encrypted;
* a post is never resent automatically: ``sending`` is committed before the
  request, and a lost answer ends as ``unknown`` for the owner to check.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import Conflict, Forbidden, ProviderUnavailable, ValidationFailed
from app.core.secretbox import SecretUnreadable, seal, unseal
from app.modules.pikirler.integrations import teswirler
from app.modules.pikirler.models import Crosspost, ExternalAccount, Thought
from app.modules.pikirler.models.enums import CrosspostStatus, CrosspostTarget, ThoughtStatus
from app.modules.pikirler.repositories import thoughts as thought_repo
from app.modules.pikirler.schemas.crosspost import CrosspostOut, TeswirlerAccountOut
from app.modules.pikirler.services import jobs
from app.modules.pikirler.services.auth import Principal

log = logging.getLogger("pikirler.crosspost")

CROSSPOST_JOB = "crosspost"
SECRET_PURPOSE = "external-account/teswirler"  # noqa: S105 -- a key label, not a secret
TESWIRLER = CrosspostTarget.teswirler.value
RETRYABLE = {CrosspostStatus.failed.value, CrosspostStatus.unknown.value}


# --- account ------------------------------------------------------------------


async def _account(
    db: AsyncSession, owner_id: uuid.UUID, *, for_update: bool = False
) -> ExternalAccount | None:
    stmt = select(ExternalAccount).where(
        ExternalAccount.owner_id == owner_id, ExternalAccount.provider == TESWIRLER
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def account_out(account: ExternalAccount | None) -> TeswirlerAccountOut:
    if account is None:
        return TeswirlerAccountOut(connected=False)
    site = get_settings().teswirler_site_url.rstrip("/")
    return TeswirlerAccountOut(
        connected=True,
        username=account.username,
        display_name=account.display_name,
        profile_url=f"{site}/@{quote(account.username, safe='')}" if account.username else None,
        connected_at=account.created_at,
    )


async def get_account(db: AsyncSession, owner_id: uuid.UUID) -> TeswirlerAccountOut:
    return account_out(await _account(db, owner_id))


def _require_owner_session(principal: Principal) -> None:
    if principal.kind != "session":
        raise Forbidden("Only the owner can do this", code="crosspost_owner_only")


async def connect(
    db: AsyncSession, principal: Principal, identifier: str, password: str
) -> TeswirlerAccountOut:
    _require_owner_session(principal)
    await ratelimit.hit(f"teswirler:connect:{principal.owner_id}", 5, 15 * 60)
    async with teswirler.TeswirlerClient() as client:
        try:
            profile = await client.login(identifier.strip(), password)
        except teswirler.LoginRejected as exc:
            raise ValidationFailed(
                "Teswirler did not accept this login", code="teswirler_login_failed"
            ) from exc
        except teswirler.TeswirlerError as exc:
            raise ProviderUnavailable(
                "Teswirler is unavailable", code="teswirler_unavailable"
            ) from exc
        assert client.session is not None
        sealed = seal(client.session.to_dict(), SECRET_PURPOSE)

    account = await _account(db, principal.owner_id, for_update=True)
    if account is None:
        account = ExternalAccount(owner_id=principal.owner_id, provider=TESWIRLER)
        db.add(account)
    account.credentials = sealed
    account.remote_user_id = profile.user_id
    account.username = profile.username
    account.display_name = profile.display_name
    await db.commit()
    await db.refresh(account)
    log.info("teswirler_connected")
    return account_out(account)


async def disconnect(db: AsyncSession, principal: Principal) -> TeswirlerAccountOut:
    _require_owner_session(principal)
    account = await _account(db, principal.owner_id, for_update=True)
    if account is None:
        return account_out(None)
    try:
        session = teswirler.Session.from_dict(unseal(account.credentials, SECRET_PURPOSE))
    except SecretUnreadable:
        session = None
    await db.execute(delete(ExternalAccount).where(ExternalAccount.id == account.id))
    await db.commit()
    if session is not None:  # best effort: end the session on Teswirler too
        async with teswirler.TeswirlerClient(session) as client:
            await client.logout()
    log.info("teswirler_disconnected")
    return account_out(None)


# --- queueing -------------------------------------------------------------------


async def queue(
    db: AsyncSession, principal: Principal, thought: Thought, targets: Sequence[str]
) -> None:
    """Called inside the transaction that creates the thought (no commit)."""
    if not targets:
        return
    _require_owner_session(principal)
    if thought.status != ThoughtStatus.published.value:
        raise ValidationFailed(
            "Only published thoughts can be shared", code="crosspost_not_published"
        )
    if not thought.content.strip():
        raise ValidationFailed("There is no text to share", code="crosspost_empty")
    for target in dict.fromkeys(targets):
        if target == TESWIRLER and await _account(db, principal.owner_id) is None:
            raise Conflict("Connect Teswirler in Settings first", code="teswirler_not_connected")
        row = Crosspost(owner_id=principal.owner_id, thought_id=thought.id, target=target)
        db.add(row)
        await db.flush()
        await _enqueue(db, row)


async def _enqueue(db: AsyncSession, row: Crosspost) -> None:
    await jobs.enqueue(
        db,
        CROSSPOST_JOB,
        {"crosspost_id": str(row.id)},
        owner_id=row.owner_id,
        dedupe_key=f"crosspost:{row.id}",
        max_attempts=1,  # never resent automatically
    )


async def share(
    db: AsyncSession, principal: Principal, thought_id: uuid.UUID, target: CrosspostTarget
) -> list[CrosspostOut]:
    """Share an existing thought, or try again after a failure."""
    _require_owner_session(principal)
    thought = await thought_repo.get(db, principal.owner_id, thought_id, for_update=True)
    row = (
        await db.execute(
            select(Crosspost)
            .where(Crosspost.thought_id == thought.id, Crosspost.target == target.value)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await queue(db, principal, thought, [target.value])
    elif row.status == CrosspostStatus.sent.value:
        raise Conflict("This thought is already there", code="crosspost_already_sent")
    elif row.status in RETRYABLE:
        if await _account(db, principal.owner_id) is None:
            raise Conflict("Connect Teswirler in Settings first", code="teswirler_not_connected")
        if not thought.content.strip():
            raise ValidationFailed("There is no text to share", code="crosspost_empty")
        row.status = CrosspostStatus.pending.value
        row.error_code = None
        row.error_message = None
        await _enqueue(db, row)
    await db.commit()
    return await for_thought(db, principal.owner_id, thought.id)


# --- presenting -----------------------------------------------------------------


def out(row: Crosspost) -> CrosspostOut:
    return CrosspostOut(
        target=CrosspostTarget(row.target),
        status=CrosspostStatus(row.status),
        url=row.remote_url,
        error_code=row.error_code,
        error_message=row.error_message,
        sent_at=row.sent_at,
        updated_at=row.updated_at,
    )


async def by_thought(
    db: AsyncSession, thought_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[CrosspostOut]]:
    if not thought_ids:
        return {}
    rows = (
        await db.execute(
            select(Crosspost)
            .where(Crosspost.thought_id.in_(list(thought_ids)))
            .order_by(Crosspost.target)
        )
    ).scalars()
    result: dict[uuid.UUID, list[CrosspostOut]] = {}
    for row in rows:
        result.setdefault(row.thought_id, []).append(out(row))
    return result


async def for_thought(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID
) -> list[CrosspostOut]:
    rows = (
        await db.execute(
            select(Crosspost)
            .where(Crosspost.owner_id == owner_id, Crosspost.thought_id == thought_id)
            .order_by(Crosspost.target)
        )
    ).scalars()
    return [out(r) for r in rows]


# --- the job ----------------------------------------------------------------------


async def _finish(
    db: AsyncSession,
    crosspost_id: uuid.UUID,
    status: CrosspostStatus,
    *,
    error: teswirler.TeswirlerError | None = None,
    error_code: str | None = None,
    published: teswirler.Published | None = None,
) -> dict[str, Any]:
    row = await db.get(Crosspost, crosspost_id, with_for_update=True)
    if row is None:
        return {"skipped": "gone"}
    row.status = status.value
    row.error_code = error.code if error else error_code
    row.error_message = (error.message or None) if error else None
    if published is not None:
        row.remote_id = published.remote_id
        row.remote_url = published.url
        row.sent_at = utcnow()
    await db.commit()
    log.info("crosspost_finished", extra={"status": status.value, "target": row.target})
    return {"status": status.value}


async def run(db: AsyncSession, crosspost_id: uuid.UUID) -> dict[str, Any]:
    row = await db.get(Crosspost, crosspost_id, with_for_update=True)
    if row is None:
        return {"skipped": "gone"}
    if row.status == CrosspostStatus.sending.value:
        # A previous attempt died after (maybe) sending: never send twice.
        row.status = CrosspostStatus.unknown.value
        row.error_code = teswirler.OutcomeUnknown.code
        await db.commit()
        return {"status": row.status}
    if row.status != CrosspostStatus.pending.value:
        return {"skipped": row.status}

    thought = (
        await db.execute(
            select(Thought).where(Thought.id == row.thought_id, Thought.owner_id == row.owner_id)
        )
    ).scalar_one_or_none()
    if thought is None or thought.deleted_at is not None or not thought.content.strip():
        row.status = CrosspostStatus.failed.value
        row.error_code = "crosspost_empty"
        await db.commit()
        return {"status": row.status}
    content = thought.content

    account = await _account(db, row.owner_id, for_update=True)
    session: teswirler.Session | None = None
    if account is not None:
        try:
            session = teswirler.Session.from_dict(unseal(account.credentials, SECRET_PURPOSE))
        except SecretUnreadable:
            session = None
    if session is None:
        row.status = CrosspostStatus.failed.value
        row.error_code = "teswirler_not_connected"
        await db.commit()
        return {"status": row.status}

    row.status = CrosspostStatus.sending.value
    await db.commit()  # from here on a crash means "unknown", not "resend"
    account_id = account.id if account else None

    async with teswirler.TeswirlerClient(session) as client:
        try:
            published = await client.publish(content)
        except teswirler.OutcomeUnknown as exc:
            await _save_session(db, account_id, client.session, session)
            return await _finish(db, crosspost_id, CrosspostStatus.unknown, error=exc)
        except teswirler.TeswirlerError as exc:
            await _save_session(db, account_id, client.session, session)
            return await _finish(db, crosspost_id, CrosspostStatus.failed, error=exc)
        await _save_session(db, account_id, client.session, session)
    return await _finish(db, crosspost_id, CrosspostStatus.sent, published=published)


async def _save_session(
    db: AsyncSession,
    account_id: uuid.UUID | None,
    current: teswirler.Session | None,
    original: teswirler.Session,
) -> None:
    """Keep refreshed tokens, so the next post does not need a new login."""
    if account_id is None or current is None or current == original:
        return
    account = await db.get(ExternalAccount, account_id, with_for_update=True)
    if account is not None:
        account.credentials = seal(current.to_dict(), SECRET_PURPOSE)
        await db.commit()


async def run_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await run(db, uuid.UUID(payload["crosspost_id"]))
