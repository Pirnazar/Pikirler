from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import DB, ReadThoughts, WriteThoughts
from app.modules.pikirler.models import Thought
from app.modules.pikirler.schemas.common import ERROR_RESPONSES
from app.modules.pikirler.schemas.organize import (
    ThreadNode,
    ThreadOut,
    ThreadTimeline,
    ThreadUpdate,
)
from app.modules.pikirler.services import presenter
from app.modules.pikirler.services import threads as service

router = APIRouter(prefix="/threads", tags=["threads"], responses=ERROR_RESPONSES)


def _depth_first(thoughts: list[Thought]) -> list[Thought]:
    """Each reply directly under its parent, siblings oldest first."""
    ids = {t.id for t in thoughts}
    children: dict[uuid.UUID | None, list[Thought]] = {}
    for t in thoughts:  # already chronological
        parent = t.parent_id if t.parent_id in ids else None
        children.setdefault(parent, []).append(t)
    ordered: list[Thought] = []
    stack = list(reversed(children.get(None, [])))
    while stack:
        node = stack.pop()
        ordered.append(node)
        stack.extend(reversed(children.get(node.id, [])))
    return ordered


async def _nodes(db: DB, ordered: list[Thought], depth: dict[uuid.UUID, int]) -> list[ThreadNode]:
    presented = await presenter.present(db, ordered)
    out = []
    for i, item in enumerate(presented):
        out.append(
            ThreadNode(
                thought=item,
                depth=depth[item.id],
                previous_id=presented[i - 1].id if i > 0 else None,
                next_id=presented[i + 1].id if i + 1 < len(presented) else None,
            )
        )
    return out


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: uuid.UUID, principal: ReadThoughts, db: DB) -> ThreadOut:
    thread = await service.get_thread(db, principal.owner_id, thread_id)
    thoughts = await service.thread_thoughts(db, principal.owner_id, thread_id)
    depth = service.depths(thoughts)
    return ThreadOut(
        id=thread.id,
        title=thread.title,
        root_thought_id=thread.root_thought_id,
        created_at=thread.created_at,
        last_activity_at=thread.last_activity_at,
        thought_count=len(thoughts),
        items=await _nodes(db, _depth_first(thoughts), depth),
    )


@router.get("/{thread_id}/timeline", response_model=ThreadTimeline)
async def thread_timeline(thread_id: uuid.UUID, principal: ReadThoughts, db: DB) -> ThreadTimeline:
    await service.get_thread(db, principal.owner_id, thread_id)
    thoughts = await service.thread_thoughts(db, principal.owner_id, thread_id)
    return ThreadTimeline(id=thread_id, items=await _nodes(db, thoughts, service.depths(thoughts)))


@router.patch("/{thread_id}", response_model=ThreadOut)
async def rename_thread(
    thread_id: uuid.UUID, body: ThreadUpdate, principal: WriteThoughts, db: DB
) -> ThreadOut:
    await service.rename(db, principal.owner_id, thread_id, body.title)
    return await get_thread(thread_id, principal, db)
