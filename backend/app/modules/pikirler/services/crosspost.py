"""Publishing a copy of a thought on another site (Teswirler, makalam).

Rules:
* a thought leaves Pikirler only when the owner ticked a target for it, in
  their own browser session (service tokens cannot cross-post);
* a site's password is used once to sign in and is never stored; the
  session it yields is kept encrypted;
* a post is never resent automatically: ``sending`` is committed before the
  request, and a lost answer ends as ``unknown`` for the owner to check.
  (makalam de-duplicates by a key, which is the cross-post's id, so sending
  it again after ``unknown`` cannot make a second post there.)
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.exceptions import Conflict, Forbidden, ProviderUnavailable, ValidationFailed
from app.core.secretbox import SecretUnreadable, seal, unseal
from app.modules.pikirler.integrations import makalam_account, remote, teswirler
from app.modules.pikirler.models import Crosspost, ExternalAccount, Thought
from app.modules.pikirler.models.enums import CrosspostStatus, CrosspostTarget, ThoughtStatus
from app.modules.pikirler.repositories import thoughts as thought_repo
from app.modules.pikirler.schemas.crosspost import CrosspostOut, SiteAccountOut
from app.modules.pikirler.services import jobs
from app.modules.pikirler.services.auth import Principal

log = logging.getLogger("pikirler.crosspost")

CROSSPOST_JOB = "crosspost"
TESWIRLER = CrosspostTarget.teswirler.value
MAKALAM = CrosspostTarget.makalam.value
RETRYABLE = {CrosspostStatus.failed.value, CrosspostStatus.unknown.value}


# --- the sites ------------------------------------------------------------------


@dataclass(frozen=True)
class Profile:
    user_id: str | None
    username: str | None
    display_name: str | None


@dataclass(frozen=True)
class Published:
    remote_id: str | None
    url: str


class Site:
    """One place a thought can be published. Sessions are plain dicts here:
    they are sealed before they touch the database."""

    target: str
    name: str

    @property
    def secret_purpose(self) -> str:
        return f"external-account/{self.target}"

    def not_connected(self) -> Conflict:
        return Conflict(
            f"Connect {self.name} in Settings first", code=f"{self.target}_not_connected"
        )

    def check(self, content: str) -> None:
        """Raise when this site cannot take ``content``."""

    def profile_url(self, username: str) -> str:
        raise NotImplementedError

    async def login(self, identifier: str, password: str) -> tuple[Profile, dict[str, Any]]:
        raise NotImplementedError

    async def publish(
        self, session: dict[str, Any], content: str, key: str
    ) -> tuple[Published, dict[str, Any] | None]:
        """The result and the session as it is now (it may have been renewed).
        Raises ``remote.RemoteError`` subclasses."""
        raise NotImplementedError

    async def logout(self, session: dict[str, Any]) -> None:
        raise NotImplementedError


class TeswirlerSite(Site):
    target = TESWIRLER
    name = "Teswirler"

    def profile_url(self, username: str) -> str:
        site = get_settings().teswirler_site_url.rstrip("/")
        return f"{site}/@{quote(username, safe='')}"

    async def login(self, identifier: str, password: str) -> tuple[Profile, dict[str, Any]]:
        async with teswirler.TeswirlerClient() as client:
            p = await client.login(identifier, password)
            assert client.session is not None
            return Profile(p.user_id, p.username, p.display_name), client.session.to_dict()

    async def publish(
        self, session: dict[str, Any], content: str, key: str
    ) -> tuple[Published, dict[str, Any] | None]:
        start = teswirler.Session.from_dict(session)
        async with teswirler.TeswirlerClient(start) as client:
            try:
                done = await client.publish(content)
            except remote.RemoteError as exc:
                exc.session = client.session.to_dict() if client.session else None
                raise
            current = client.session.to_dict() if client.session else None
        return Published(done.remote_id, done.url), current

    async def logout(self, session: dict[str, Any]) -> None:
        async with teswirler.TeswirlerClient(teswirler.Session.from_dict(session)) as client:
            await client.logout()


class MakalamSite(Site):
    target = MAKALAM
    name = "makalam"

    def check(self, content: str) -> None:
        if len(makalam_account.split_chain(content)) > makalam_account.CHAIN_LIMIT:
            raise ValidationFailed(
                "This thought is too long for makalam (25 posts of 500 characters)",
                code="makalam_too_long",
            )

    def profile_url(self, username: str) -> str:
        site = get_settings().makalam_site_url.rstrip("/")
        return f"{site}/@{quote(username, safe='')}/"

    async def login(self, identifier: str, password: str) -> tuple[Profile, dict[str, Any]]:
        async with makalam_account.MakalamClient() as client:
            p = await client.login(identifier, password)
            assert client.session is not None
            return Profile(p.user_id, p.username, p.display_name), client.session.to_dict()

    async def publish(
        self, session: dict[str, Any], content: str, key: str
    ) -> tuple[Published, dict[str, Any] | None]:
        parts = makalam_account.split_chain(content)
        if not parts or len(parts) > makalam_account.CHAIN_LIMIT:
            raise makalam_account.Rejected("too long for makalam")
        start = makalam_account.Session.from_dict(session)
        async with makalam_account.MakalamClient(start) as client:
            try:
                done = await client.publish(parts, key)
            except remote.RemoteError as exc:
                exc.session = client.session.to_dict() if client.session else None
                raise
            current = client.session.to_dict() if client.session else None
        return Published(done.remote_id, done.url), current

    async def logout(self, session: dict[str, Any]) -> None:
        start = makalam_account.Session.from_dict(session)
        async with makalam_account.MakalamClient(start) as client:
            await client.logout()


SITES: dict[str, Site] = {TESWIRLER: TeswirlerSite(), MAKALAM: MakalamSite()}


def site_of(target: str | CrosspostTarget) -> Site:
    return SITES[CrosspostTarget(target).value]


# --- account ------------------------------------------------------------------


async def _account(
    db: AsyncSession, owner_id: uuid.UUID, target: str, *, for_update: bool = False
) -> ExternalAccount | None:
    stmt = select(ExternalAccount).where(
        ExternalAccount.owner_id == owner_id, ExternalAccount.provider == target
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


def account_out(site: Site, account: ExternalAccount | None) -> SiteAccountOut:
    if account is None:
        return SiteAccountOut(site=CrosspostTarget(site.target), connected=False)
    return SiteAccountOut(
        site=CrosspostTarget(site.target),
        connected=True,
        username=account.username,
        display_name=account.display_name,
        profile_url=site.profile_url(account.username) if account.username else None,
        connected_at=account.created_at,
    )


async def get_account(
    db: AsyncSession, owner_id: uuid.UUID, target: CrosspostTarget
) -> SiteAccountOut:
    site = site_of(target)
    return account_out(site, await _account(db, owner_id, site.target))


def _require_owner_session(principal: Principal) -> None:
    if principal.kind != "session":
        raise Forbidden("Only the owner can do this", code="crosspost_owner_only")


async def connect(
    db: AsyncSession,
    principal: Principal,
    target: CrosspostTarget,
    identifier: str,
    password: str,
) -> SiteAccountOut:
    _require_owner_session(principal)
    site = site_of(target)
    await ratelimit.hit(f"{site.target}:connect:{principal.owner_id}", 5, 15 * 60)
    try:
        profile, session = await site.login(identifier.strip(), password)
    except remote.LoginRejected as exc:
        raise ValidationFailed(
            f"{site.name} did not accept this login", code=f"{site.target}_login_failed"
        ) from exc
    except remote.RemoteError as exc:
        raise ProviderUnavailable(
            f"{site.name} is unavailable", code=f"{site.target}_unavailable"
        ) from exc
    sealed = seal(session, site.secret_purpose)

    account = await _account(db, principal.owner_id, site.target, for_update=True)
    if account is None:
        account = ExternalAccount(owner_id=principal.owner_id, provider=site.target)
        db.add(account)
    account.credentials = sealed
    account.remote_user_id = profile.user_id
    account.username = profile.username
    account.display_name = profile.display_name
    await db.commit()
    await db.refresh(account)
    log.info("crosspost_account_connected", extra={"target": site.target})
    return account_out(site, account)


async def disconnect(
    db: AsyncSession, principal: Principal, target: CrosspostTarget
) -> SiteAccountOut:
    _require_owner_session(principal)
    site = site_of(target)
    account = await _account(db, principal.owner_id, site.target, for_update=True)
    if account is None:
        return account_out(site, None)
    try:
        session: dict[str, Any] | None = unseal(account.credentials, site.secret_purpose)
    except SecretUnreadable:
        session = None
    await db.execute(delete(ExternalAccount).where(ExternalAccount.id == account.id))
    await db.commit()
    if session is not None:  # best effort: end the session on the site too
        with contextlib.suppress(remote.RemoteError):
            await site.logout(session)
    log.info("crosspost_account_disconnected", extra={"target": site.target})
    return account_out(site, None)


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
        site = site_of(target)
        if await _account(db, principal.owner_id, site.target) is None:
            raise site.not_connected()
        site.check(thought.content)
        row = Crosspost(owner_id=principal.owner_id, thought_id=thought.id, target=site.target)
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
    site = site_of(target)
    thought = await thought_repo.get(db, principal.owner_id, thought_id, for_update=True)
    row = (
        await db.execute(
            select(Crosspost)
            .where(Crosspost.thought_id == thought.id, Crosspost.target == site.target)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await queue(db, principal, thought, [site.target])
    elif row.status == CrosspostStatus.sent.value:
        raise Conflict("This thought is already there", code="crosspost_already_sent")
    elif row.status in RETRYABLE:
        if await _account(db, principal.owner_id, site.target) is None:
            raise site.not_connected()
        if not thought.content.strip():
            raise ValidationFailed("There is no text to share", code="crosspost_empty")
        site.check(thought.content)
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
    error: remote.RemoteError | None = None,
    error_code: str | None = None,
    published: Published | None = None,
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
    site = site_of(row.target)
    if row.status == CrosspostStatus.sending.value:
        # A previous attempt died after (maybe) sending: never send twice.
        row.status = CrosspostStatus.unknown.value
        row.error_code = f"{site.target}_outcome_unknown"
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

    account = await _account(db, row.owner_id, site.target, for_update=True)
    session: dict[str, Any] | None = None
    if account is not None:
        try:
            session = unseal(account.credentials, site.secret_purpose)
        except SecretUnreadable:
            session = None
    if session is None:
        row.status = CrosspostStatus.failed.value
        row.error_code = f"{site.target}_not_connected"
        await db.commit()
        return {"status": row.status}

    row.status = CrosspostStatus.sending.value
    await db.commit()  # from here on a crash means "unknown", not "resend"
    account_id = account.id if account else None

    try:
        published, current = await site.publish(session, content, str(crosspost_id))
    except remote.OutcomeUnknown as exc:
        await _save_session(db, site, account_id, exc.session, session)
        return await _finish(db, crosspost_id, CrosspostStatus.unknown, error=exc)
    except remote.RemoteError as exc:
        await _save_session(db, site, account_id, exc.session, session)
        return await _finish(db, crosspost_id, CrosspostStatus.failed, error=exc)
    await _save_session(db, site, account_id, current, session)
    return await _finish(db, crosspost_id, CrosspostStatus.sent, published=published)


async def _save_session(
    db: AsyncSession,
    site: Site,
    account_id: uuid.UUID | None,
    current: dict[str, Any] | None,
    original: dict[str, Any],
) -> None:
    """Keep renewed tokens or cookies, so the next post needs no new login."""
    if account_id is None or not current or current == original:
        return
    account = await db.get(ExternalAccount, account_id, with_for_update=True)
    if account is not None:
        account.credentials = seal(current, site.secret_purpose)
        await db.commit()


async def run_job(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await run(db, uuid.UUID(payload["crosspost_id"]))
