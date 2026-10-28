"""Administrative commands. There is no registration endpoint: the owner is
created here, on the server.

    pikirler create-owner --email me@example.com
    pikirler create-service-client --email me@example.com --name jemshit \\
        --source jemshit --scopes thoughts:read,thoughts:write,search,voice:write
    pikirler revoke-service-client --email me@example.com --name jemshit
    pikirler create-vector-index            # ANN index for the configured embedding model
    pikirler reindex-embeddings --email me@example.com [--force]
    pikirler dispatch-jobs                  # push due outbox jobs to the queue now
    pikirler export-openapi openapi.json
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import sys
from typing import Any

from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.core.database import get_database, utcnow
from app.modules.pikirler.models import Owner, ServiceClient
from app.modules.pikirler.services import auth, jobs


async def _owner(db: Any, email: str) -> Owner:
    owner: Owner | None = (
        await db.execute(select(Owner).where(Owner.email.ilike(email.strip())))
    ).scalar_one_or_none()
    if owner is None:
        sys.exit(f"No owner with email {email}")
    return owner


def _read_secret(prompt: str, min_length: int) -> str:
    first = getpass.getpass(prompt)
    if len(first) < min_length:
        sys.exit(f"Must be at least {min_length} characters")
    if getpass.getpass("Repeat: ") != first:
        sys.exit("Values do not match")
    return first


async def create_owner(args: argparse.Namespace) -> None:
    password = _read_secret("Password: ", 12)
    async with get_database().sessionmaker() as db:
        owner = await auth.create_owner(
            db, args.email, password, args.name, args.timezone or get_settings().default_timezone
        )
    print(f"Owner created: {owner.id}")


async def set_password(args: argparse.Namespace) -> None:
    from app.core.security import hash_secret

    password = _read_secret("New password: ", 12)
    async with get_database().sessionmaker() as db:
        owner = await _owner(db, args.email)
        owner.password_hash = hash_secret(password)
        owner.password_changed_at = utcnow()
        owner.failed_login_count = 0
        owner.locked_until = None
        await db.commit()
        await auth.revoke_all_sessions(db, owner.id)
    print("Password changed; all sessions revoked.")


async def create_service_client(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    async with get_database().sessionmaker() as db:
        owner = await _owner(db, args.email)
        _, token = await auth.create_service_client(db, owner.id, args.name, scopes, args.source)
    print("Service token (shown once, store it in the client's secret store):")
    print(token)


async def revoke_service_client(args: argparse.Namespace) -> None:
    async with get_database().sessionmaker() as db:
        owner = await _owner(db, args.email)
        result = await db.execute(
            update(ServiceClient)
            .where(
                ServiceClient.owner_id == owner.id,
                ServiceClient.name == args.name,
                ServiceClient.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        await db.commit()
    print("Revoked." if result.rowcount else "No active client with that name.")  # type: ignore[attr-defined]


async def create_vector_index(args: argparse.Namespace) -> None:
    """HNSW index for one model's vectors (partial on model, cast to its dimension).
    Worth it from a few thousand thoughts on; exact search is used before that."""
    from app.modules.pikirler.integrations.embeddings import get_embedding_provider

    provider = get_embedding_provider()
    slug = re.sub(r"[^a-z0-9]+", "_", provider.model.lower()).strip("_")[:40]
    name = f"ix_thought_embeddings_ann_{slug}_{provider.dimensions}"
    sql = (
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON thought_embeddings "
        f"USING hnsw ((embedding::vector({int(provider.dimensions)})) vector_cosine_ops) "
        f"WHERE model = :model"
    )
    engine = get_database().engine
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        # CREATE INDEX cannot take bind parameters inside the predicate.
        model_literal = provider.model.replace("'", "''")
        await conn.execute(text(sql.replace(":model", f"'{model_literal}'")))
    print(f"Index ready: {name}")


async def reindex_embeddings(args: argparse.Namespace) -> None:
    from app.modules.pikirler.tasks.handlers import REINDEX_JOB

    async with get_database().sessionmaker() as db:
        owner = await _owner(db, args.email)
        job_id = await jobs.enqueue(
            db, REINDEX_JOB, {"owner_id": str(owner.id), "force": args.force}, owner_id=owner.id
        )
        await db.commit()
        await jobs.kick(db)
    print(f"Reindex job queued: {job_id}")


async def dispatch_jobs(args: argparse.Namespace) -> None:
    async with get_database().sessionmaker() as db:
        print(f"Dispatched {await jobs.dispatch_due(db)} jobs")


async def init_storage(args: argparse.Namespace) -> None:
    from app.storage import get_storage
    from app.storage.s3 import S3Storage

    storage = get_storage()
    if not isinstance(storage, S3Storage):
        print(f"Storage backend is {storage.name}; nothing to initialise.")
        return
    created = await storage.ensure_bucket()
    print(f"Bucket {storage.bucket} {'created' if created else 'already exists'}.")


def _s3_storage() -> Any:
    from app.storage import get_storage
    from app.storage.s3 import S3Storage

    storage = get_storage()
    if not isinstance(storage, S3Storage):
        sys.exit("This command needs PIKIRLER_STORAGE_BACKEND=s3.")
    return storage


async def dump_objects(args: argparse.Namespace) -> None:
    """Write every stored file to stdout as a tar stream (used by backup.sh)."""
    storage = _s3_storage()
    count = await asyncio.to_thread(storage.export_tar, sys.stdout.buffer)
    print(f"Exported {count} objects", file=sys.stderr)


async def load_objects(args: argparse.Namespace) -> None:
    """Read a tar stream made by dump-objects from stdin (used by restore.sh)."""
    storage = _s3_storage()
    await storage.ensure_bucket()
    count = await asyncio.to_thread(storage.import_tar, sys.stdin.buffer)
    print(f"Imported {count} objects", file=sys.stderr)


async def export_openapi(args: argparse.Namespace) -> None:
    from app.main import app

    def write() -> None:
        with open(args.path, "w", encoding="utf-8") as fh:
            json.dump(app.openapi(), fh, ensure_ascii=False, indent=2)

    await asyncio.to_thread(write)
    print(f"Wrote {args.path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="pikirler")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-owner")
    p.add_argument("--email", required=True)
    p.add_argument("--name")
    p.add_argument("--timezone")
    p.set_defaults(func=create_owner)

    p = sub.add_parser("set-password")
    p.add_argument("--email", required=True)
    p.set_defaults(func=set_password)

    p = sub.add_parser("create-service-client")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--source", default="jemshit", choices=["jemshit", "telegram", "mobile", "api"])
    p.add_argument(
        "--scopes", required=True, help=f"comma-separated: {sorted(auth.SERVICE_SCOPES)}"
    )
    p.set_defaults(func=create_service_client)

    p = sub.add_parser("revoke-service-client")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(func=revoke_service_client)

    sub.add_parser("create-vector-index").set_defaults(func=create_vector_index)

    p = sub.add_parser("reindex-embeddings")
    p.add_argument("--email", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=reindex_embeddings)

    sub.add_parser("dispatch-jobs").set_defaults(func=dispatch_jobs)

    sub.add_parser("init-storage").set_defaults(func=init_storage)
    sub.add_parser("dump-objects").set_defaults(func=dump_objects)
    sub.add_parser("load-objects").set_defaults(func=load_objects)

    p = sub.add_parser("export-openapi")
    p.add_argument("path")
    p.set_defaults(func=export_openapi)

    args = parser.parse_args()

    async def run() -> None:
        try:
            await args.func(args)
        finally:
            await jobs.close_pool()
            await get_database().dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
