"""Keyword (FTS + trigram fallback), semantic (pgvector) and hybrid (RRF) search.

Keyword search never depends on an AI provider. Semantic search is an add-on:
when the embedding provider is disabled, failing or not permitted, hybrid
search returns keyword results and says why semantic was skipped.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG as _REGCONFIG
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, Forbidden, NotFound, ProviderUnavailable
from app.core.text import escape_like, prepare_query
from app.modules.pikirler.integrations.embeddings import (
    EmbeddingProvider,
    embeddings_enabled,
    ensure_allowed,
    get_embedding_provider,
)
from app.modules.pikirler.models import Owner, Thought, ThoughtEmbedding
from app.modules.pikirler.repositories import thoughts as repo
from app.modules.pikirler.repositories.thoughts import ThoughtFilters

log = logging.getLogger("pikirler.search")

RRF_K = 60
CANDIDATES = 50
FUZZY_THRESHOLD = 0.45


@dataclass
class Hit:
    thought_id: uuid.UUID
    score: float
    match_type: str  # keyword | fuzzy | semantic | hybrid


@dataclass
class SearchOutcome:
    hits: list[Hit]
    semantic: str = "not_requested"  # ok | disabled | unavailable | not_permitted | ...
    extra: dict[str, Any] = field(default_factory=dict)


def _query_expr(norm: str, folded: str):  # type: ignore[no-untyped-def]
    simple = func.websearch_to_tsquery(cast(literal("simple"), _REGCONFIG), norm)
    russian = func.websearch_to_tsquery(cast(literal("russian"), _REGCONFIG), norm)
    folded_q = func.websearch_to_tsquery(cast(literal("simple"), _REGCONFIG), folded)
    return simple.op("||")(russian).op("||")(folded_q)


async def keyword(
    db: AsyncSession, owner_id: uuid.UUID, query: str, filters: ThoughtFilters, limit: int
) -> list[Hit]:
    norm, folded = prepare_query(query)
    if not norm:
        return []
    tsq = _query_expr(norm, folded)
    rank = func.ts_rank_cd(Thought.search_vector, tsq)
    stmt = (
        repo.apply_filters(
            select(Thought.id, rank.label("rank")).where(Thought.owner_id == owner_id),
            owner_id,
            filters,
        )
        .where(Thought.search_vector.op("@@")(tsq))
        .order_by(rank.desc(), Thought.created_at.desc())
        .limit(limit)
    )
    hits = [Hit(r.id, float(r.rank), "keyword") for r in (await db.execute(stmt)).all()]
    if hits:
        return hits
    return await _fuzzy(db, owner_id, norm, filters, limit)


async def _fuzzy(
    db: AsyncSession, owner_id: uuid.UUID, norm: str, filters: ThoughtFilters, limit: int
) -> list[Hit]:
    """Substring and typo-tolerant fallback (pg_trgm) when FTS finds nothing:
    partial words, unknown morphology, misspellings."""
    plain = norm.replace('"', "").strip()
    if not plain:
        return []
    sim = func.word_similarity(plain, Thought.search_document)
    stmt = (
        repo.apply_filters(
            select(Thought.id, sim.label("sim")).where(Thought.owner_id == owner_id),
            owner_id,
            filters,
        )
        .where(
            Thought.search_document.ilike("%" + escape_like(plain) + "%", escape="\\")
            | (sim >= FUZZY_THRESHOLD)
        )
        .order_by(sim.desc(), Thought.created_at.desc())
        .limit(limit)
    )
    return [Hit(r.id, float(r.sim), "fuzzy") for r in (await db.execute(stmt)).all()]


def _vector_distance(provider: EmbeddingProvider, vector: Sequence[float]):  # type: ignore[no-untyped-def]
    column = cast(ThoughtEmbedding.embedding, Vector(provider.dimensions))
    return column.cosine_distance(
        cast(literal(list(vector), Vector(provider.dimensions)), Vector(provider.dimensions))
    )


async def _nearest(
    db: AsyncSession,
    owner_id: uuid.UUID,
    provider: EmbeddingProvider,
    vector: Sequence[float],
    filters: ThoughtFilters,
    limit: int,
    exclude: uuid.UUID | None = None,
) -> list[Hit]:
    distance = _vector_distance(provider, vector)
    stmt = repo.apply_filters(
        select(Thought.id, distance.label("distance"))
        .join(ThoughtEmbedding, ThoughtEmbedding.thought_id == Thought.id)
        .where(
            Thought.owner_id == owner_id,
            ThoughtEmbedding.owner_id == owner_id,
            ThoughtEmbedding.model == provider.model,
            ThoughtEmbedding.dimensions == provider.dimensions,
        ),
        owner_id,
        filters,
    )
    if exclude is not None:
        stmt = stmt.where(Thought.id != exclude)
    stmt = stmt.order_by(distance).limit(limit)
    return [Hit(r.id, 1.0 - float(r.distance), "semantic") for r in (await db.execute(stmt)).all()]


async def _semantic_provider(db: AsyncSession, owner_id: uuid.UUID) -> EmbeddingProvider:
    provider = get_embedding_provider()
    owner = await db.get(Owner, owner_id)
    ensure_allowed(provider, bool(owner and owner.ai_external_enabled))
    return provider


async def semantic(
    db: AsyncSession, owner_id: uuid.UUID, query: str, filters: ThoughtFilters, limit: int
) -> list[Hit]:
    provider = await _semantic_provider(db, owner_id)
    vector = await provider.embed_query(query)
    return await _nearest(db, owner_id, provider, vector, filters, limit)


def _status_of(exc: AppError) -> str:
    if isinstance(exc, Forbidden):
        return "not_permitted"
    if exc.code == "embeddings_disabled":
        return "disabled"
    return "unavailable"


async def hybrid(
    db: AsyncSession, owner_id: uuid.UUID, query: str, filters: ThoughtFilters, limit: int
) -> SearchOutcome:
    """Reciprocal Rank Fusion of keyword and semantic rankings:
    score(d) = sum over rankings of 1 / (k + rank_d), k = 60."""
    keyword_hits = await keyword(db, owner_id, query, filters, CANDIDATES)
    semantic_hits: list[Hit] = []
    status = "ok"
    if not embeddings_enabled():
        status = "disabled"
    else:
        try:
            semantic_hits = await semantic(db, owner_id, query, filters, CANDIDATES)
        except (ProviderUnavailable, Forbidden) as exc:
            status = _status_of(exc)
        except Exception as exc:  # a provider bug must not break search
            log.warning("semantic_search_failed", extra={"error_type": type(exc).__name__})
            status = "unavailable"

    scores: dict[uuid.UUID, float] = {}
    sources: dict[uuid.UUID, set[str]] = {}
    for ranking in (keyword_hits, semantic_hits):
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.thought_id] = scores.get(hit.thought_id, 0.0) + 1.0 / (RRF_K + rank)
            sources.setdefault(hit.thought_id, set()).add(hit.match_type)
    ordered = sorted(scores, key=lambda tid: scores[tid], reverse=True)[:limit]
    hits = [
        Hit(
            tid,
            round(scores[tid], 6),
            "hybrid" if len(sources[tid]) > 1 else next(iter(sources[tid])),
        )
        for tid in ordered
    ]
    return SearchOutcome(hits=hits, semantic=status)


async def similar(
    db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID, limit: int
) -> SearchOutcome:
    """Neighbours of a thought. Uses its stored vector (no provider call); falls
    back to keyword overlap when the thought has no embedding yet."""
    thought = await repo.get(db, owner_id, thought_id)
    filters = ThoughtFilters()
    if embeddings_enabled():
        try:
            provider = get_embedding_provider()
            stored = (
                await db.execute(
                    select(ThoughtEmbedding.embedding).where(
                        ThoughtEmbedding.thought_id == thought.id,
                        ThoughtEmbedding.model == provider.model,
                        ThoughtEmbedding.dimensions == provider.dimensions,
                    )
                )
            ).scalar_one_or_none()
            if stored is not None:
                hits = await _nearest(
                    db, owner_id, provider, list(stored), filters, limit, exclude=thought.id
                )
                return SearchOutcome(hits=hits, semantic="ok")
        except ProviderUnavailable:
            pass
    words = [w for w in dict.fromkeys(thought.content.split()) if len(w) > 3][:12]
    if not words:
        return SearchOutcome(hits=[], semantic="no_embedding")
    query = " or ".join(w.strip('.,!?;:"()[]') for w in words)
    hits = [
        h
        for h in await keyword(db, owner_id, query, filters, limit + 1)
        if h.thought_id != thought.id
    ][:limit]
    return SearchOutcome(hits=hits, semantic="no_embedding")


# --- indexing (worker) -------------------------------------------------------


def embedding_input(thought: Thought, tag_names: Sequence[str]) -> str:
    parts = [thought.content.strip()]
    if tag_names:
        parts.append("Tags: " + ", ".join(tag_names))
    return "\n".join(p for p in parts if p)


async def index_thought(db: AsyncSession, thought_id: uuid.UUID) -> dict[str, Any]:
    thought = await db.get(Thought, thought_id)
    if thought is None or thought.deleted_at is not None:
        return {"skipped": "thought gone"}
    provider = get_embedding_provider()
    owner = await db.get(Owner, thought.owner_id)
    try:
        ensure_allowed(provider, bool(owner and owner.ai_external_enabled))
    except Forbidden:
        return {"skipped": "external AI not permitted"}
    from app.modules.pikirler.models import Tag, ThoughtTag
    from app.modules.pikirler.services.thoughts import transcripts_for

    tag_names = list(
        (
            await db.execute(
                select(Tag.name)
                .join(ThoughtTag, ThoughtTag.tag_id == Tag.id)
                .where(ThoughtTag.thought_id == thought.id)
            )
        ).scalars()
    )
    transcripts = (await transcripts_for(db, [thought.id])).get(thought.id, [])
    # A voice thought's meaning is mostly in its transcript.
    text = "\n".join(p for p in [embedding_input(thought, tag_names), *transcripts] if p)
    if not text.strip():
        return {"skipped": "no text"}
    digest = hashlib.sha256(f"{provider.model}:{text}".encode()).hexdigest()
    existing = (
        await db.execute(
            select(ThoughtEmbedding.content_hash).where(
                ThoughtEmbedding.thought_id == thought.id,
                ThoughtEmbedding.model == provider.model,
            )
        )
    ).scalar_one_or_none()
    if existing == digest:
        return {"skipped": "unchanged"}
    vector = await provider.embed_text(text)
    await db.execute(
        insert(ThoughtEmbedding)
        .values(
            id=uuid.uuid4(),
            owner_id=thought.owner_id,
            thought_id=thought.id,
            model=provider.model,
            dimensions=provider.dimensions,
            content_hash=digest,
            embedding=vector,
        )
        .on_conflict_do_update(
            constraint="uq_thought_embeddings_thought_model",
            set_={
                "embedding": vector,
                "content_hash": digest,
                "dimensions": provider.dimensions,
                "updated_at": func.now(),
            },
        )
    )
    await db.commit()
    return {"status": "indexed", "model": provider.model, "dimensions": provider.dimensions}


async def ensure_thought(db: AsyncSession, owner_id: uuid.UUID, thought_id: uuid.UUID) -> None:
    if not await repo.owned_ids(db, owner_id, [thought_id]):
        raise NotFound("Thought not found", code="thought_not_found")
