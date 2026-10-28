"""Alembic environment.

Runs on an async engine and takes a PostgreSQL advisory lock first, so two
processes started at the same time (two replicas, a stray manual run) apply
migrations one after the other instead of racing.
"""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.modules.pikirler.models  # noqa: F401  (registers tables)
from app.core.database import Base

MIGRATION_LOCK_ID = 727_101_001

target_metadata = Base.metadata


def _database_url() -> str:
    url = context.config.attributes.get("database_url") or os.environ.get("PIKIRLER_DATABASE_URL")
    if not url:
        from app.core.config import get_settings

        url = get_settings().database_url
    return str(url)


def _include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # Per-model ANN indexes are created by `pikirler create-vector-index`.
    return not (type_ == "index" and name and str(name).startswith("ix_thought_embeddings_ann_"))


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": MIGRATION_LOCK_ID})
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": MIGRATION_LOCK_ID})
        connection.commit()


async def _run_async() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_async())
