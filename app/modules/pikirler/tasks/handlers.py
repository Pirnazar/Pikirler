"""Job kinds and their (idempotent) handlers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.pikirler.integrations.embeddings import embeddings_enabled, get_embedding_provider
from app.modules.pikirler.models import Thought, ThoughtEmbedding
from app.modules.pikirler.services import jobs, media, media_processing, search, voice
from app.modules.pikirler.services.thoughts import EMBED_JOB
from app.modules.pikirler.tasks.runner import register

REINDEX_JOB = "reindex_embeddings"


async def embed_thought(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    if not embeddings_enabled():
        return {"skipped": "embeddings disabled"}
    return await search.index_thought(db, uuid.UUID(payload["thought_id"]))


async def transcribe_audio(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await voice.run_transcription(db, uuid.UUID(payload["transcription_id"]))


async def transcription_gave_up(db: AsyncSession, payload: dict[str, Any], error: str) -> None:
    await voice.mark_failed(db, uuid.UUID(payload["transcription_id"]), error)


async def process_attachment(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    return await media_processing.process(db, uuid.UUID(payload["attachment_id"]))


async def reindex_embeddings(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    """Queue embedding jobs for thoughts without a vector of the current model
    (after switching models, or for thoughts written while AI was off)."""
    if not embeddings_enabled():
        return {"skipped": "embeddings disabled"}
    provider = get_embedding_provider()
    owner_id = uuid.UUID(payload["owner_id"])
    has_current = select(ThoughtEmbedding.thought_id).where(
        ThoughtEmbedding.model == provider.model, ThoughtEmbedding.dimensions == provider.dimensions
    )
    stmt = select(Thought.id).where(Thought.owner_id == owner_id, Thought.deleted_at.is_(None))
    if not payload.get("force"):
        stmt = stmt.where(Thought.id.not_in(has_current))
    ids = list((await db.execute(stmt)).scalars())
    for thought_id in ids:
        await jobs.enqueue(
            db,
            EMBED_JOB,
            {"thought_id": str(thought_id)},
            owner_id=owner_id,
            dedupe_key=f"embed:{thought_id}",
        )
    await db.commit()
    await jobs.kick(db)
    return {"queued": len(ids), "model": provider.model}


def register_all() -> None:
    register(EMBED_JOB, embed_thought)
    register(voice.TRANSCRIBE_JOB, transcribe_audio, on_dead=transcription_gave_up)
    register(media.PROCESS_JOB, process_attachment)
    register(REINDEX_JOB, reindex_embeddings)
    from app.modules.pikirler.services import data_export

    register(data_export.EXPORT_JOB, data_export.run_export_job)
    register(data_export.IMPORT_JOB, data_export.run_import_job)


register_all_done = False


def ensure_registered() -> None:
    global register_all_done
    if not register_all_done:
        register_all()
        register_all_done = True
