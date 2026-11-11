from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    auth,
    cinema,
    crosspost,
    data,
    integrations,
    links,
    media,
    media_search,
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
    youtube_archive,
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
api_router.include_router(media_search.router)
api_router.include_router(youtube_archive.router)
api_router.include_router(cinema.router)
api_router.include_router(voice.router)
api_router.include_router(memories.router)
api_router.include_router(ai.router)
api_router.include_router(data.router)
api_router.include_router(integrations.router)
api_router.include_router(crosspost.router)
api_router.include_router(links.router)
