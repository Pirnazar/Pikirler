from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    data,
    integrations,
    media,
    memories,
    projects,
    relations,
    search,
    system,
    thoughts,
    threads,
    timeline,
    topics,
    voice,
)

api_router = APIRouter()
api_router.include_router(system.health_router)
api_router.include_router(auth.router)
api_router.include_router(search.router)
api_router.include_router(thoughts.router)
api_router.include_router(threads.router)
api_router.include_router(topics.router)
api_router.include_router(projects.router)
api_router.include_router(relations.router)
api_router.include_router(timeline.router)
api_router.include_router(system.router)
api_router.include_router(media.router)
api_router.include_router(voice.router)
api_router.include_router(memories.router)
api_router.include_router(ai.router)
api_router.include_router(data.router)
api_router.include_router(integrations.router)
