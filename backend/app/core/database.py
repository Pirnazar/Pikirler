"""Async SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings, get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Database:
    """Owns the engine and session factory; one per process."""

    def __init__(self, settings: Settings, url: str | None = None) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url or settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            echo=settings.database_echo,
        )
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()


_db: Database | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        _db = Database(get_settings())
    return _db


def set_database(db: Database | None) -> None:
    global _db
    _db = db


async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_database().sessionmaker() as session:
        yield session
