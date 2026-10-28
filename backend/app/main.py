"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import get_database
from app.core.exceptions import install_exception_handlers
from app.core.logging import configure_logging
from app.core.redis import close_redis
from app.modules.pikirler.services import jobs

log = logging.getLogger("pikirler.http")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await jobs.close_pool()
    await close_redis()
    await get_database().dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    app = FastAPI(
        title="Pikirler API",
        version="0.1.0",
        description="Private microblog and personal knowledge store of a single owner. "
        "Part of the Second Brain ecosystem.",
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=None,
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )
    install_exception_handlers(app)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=[
                "Content-Type",
                settings.csrf_header_name,
                "Authorization",
                "Idempotency-Key",
            ],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id[:64]
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-store")
        route = request.scope.get("route")
        # Route template only (/thoughts/{thought_id}); never query strings or bodies.
        log.info(
            "request",
            extra={
                "request_id": request.state.request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
