"""Integration test harness: a real PostgreSQL (with pgvector, pg_trgm and the
migrations applied), a real Redis and real files on disk. Only external AI/ASR
providers are replaced by test doubles."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

TEST_DB_URL = os.environ.get("PIKIRLER_TEST_DATABASE_URL")
TEST_REDIS_URL = os.environ.get("PIKIRLER_TEST_REDIS_URL", "redis://localhost:6460/15")
if not TEST_DB_URL:
    raise RuntimeError(
        "Set PIKIRLER_TEST_DATABASE_URL (docker compose run --rm api pytest sets it)"
    )

MEDIA_DIR = tempfile.mkdtemp(prefix="pikirler-media-")
os.environ.update(
    {
        "PIKIRLER_ENVIRONMENT": "test",
        "PIKIRLER_DATABASE_URL": TEST_DB_URL,
        "PIKIRLER_REDIS_URL": TEST_REDIS_URL,
        "PIKIRLER_STORAGE_BACKEND": "local",
        "PIKIRLER_LOCAL_STORAGE_PATH": MEDIA_DIR,
        "PIKIRLER_COOKIE_SECURE": "false",
        "PIKIRLER_EMBEDDING_PROVIDER": "disabled",
        "PIKIRLER_LLM_PROVIDER": "disabled",
        "PIKIRLER_ASR_PROVIDER": "disabled",
        "PIKIRLER_CORS_ORIGINS": "",
    }
)
os.environ.setdefault("PIKIRLER_SECRET_KEY", "test-secret-key-" + "x" * 40)

import asyncpg  # noqa: E402
import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, get_database  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.pikirler.integrations import asr, embeddings, llm  # noqa: E402
from app.modules.pikirler.services import auth as auth_service  # noqa: E402
from app.modules.pikirler.tasks.handlers import ensure_registered  # noqa: E402

ensure_registered()

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "correct horse battery staple"
OTHER_EMAIL = "intruder@example.com"
OTHER_PASSWORD = "another long password 42"
PREFIX = get_settings().api_prefix


async def _ensure_database() -> None:
    url = make_url(TEST_DB_URL)
    conn = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host,
        port=url.port or 5432,
        database="postgres",
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", url.database)
        if not exists:
            await conn.execute(
                f'CREATE DATABASE "{url.database}" TEMPLATE template0 '
                "ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8'"
            )
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
async def database() -> AsyncIterator[None]:
    await _ensure_database()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
    )
    yield
    await get_database().dispose()


@pytest.fixture(autouse=True)
async def clean_state(database: None) -> AsyncIterator[None]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with get_database().engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await get_redis().flushdb()
    yield
    embeddings.set_embedding_provider(None)
    llm.set_llm_provider(None)
    asr.set_transcription_provider(None)


@pytest.fixture
async def db() -> AsyncIterator[object]:
    async with get_database().sessionmaker() as session:
        yield session


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver" + PREFIX
    )


@pytest.fixture
async def anon() -> AsyncIterator[httpx.AsyncClient]:
    async with _client() as client:
        yield client


async def make_owner(email: str, password: str):  # type: ignore[no-untyped-def]
    async with get_database().sessionmaker() as session:
        return await auth_service.create_owner(session, email, password, None, "Asia/Ashgabat")


async def login(email: str, password: str) -> httpx.AsyncClient:
    client = _client()
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
    return client


@pytest.fixture
async def owner():  # type: ignore[no-untyped-def]
    return await make_owner(OWNER_EMAIL, OWNER_PASSWORD)


@pytest.fixture
async def client(owner) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    c = await login(OWNER_EMAIL, OWNER_PASSWORD)
    yield c
    await c.aclose()


@pytest.fixture
async def other_client() -> AsyncIterator[httpx.AsyncClient]:
    await make_owner(OTHER_EMAIL, OTHER_PASSWORD)
    c = await login(OTHER_EMAIL, OTHER_PASSWORD)
    yield c
    await c.aclose()


async def new_thought(
    client: httpx.AsyncClient, content: str = "A thought", **extra: object
) -> dict:  # type: ignore[type-arg]
    resp = await client.post("/thoughts", json={"content": content, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]
