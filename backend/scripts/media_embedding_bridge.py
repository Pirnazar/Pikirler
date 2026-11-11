"""One-off SSH stdio bridge for initial indexing on another owner-controlled computer.

Run inside the API container. Text only goes to the SSH peer, never to logs.
The normal local server encoder remains responsible for queries and daily updates.
"""

import asyncio
import json
import math
import sys
import uuid

from sqlalchemy import case, or_, select

from app.core.config import get_settings
from app.core.database import get_database
from app.modules.pikirler.models.media_search import MediaEntry, MediaPassage


async def main():
    settings = get_settings()
    owner = uuid.UUID(settings.youtube_archive_owner_id)
    model = settings.media_encoder_model
    database = get_database()
    total = 0
    try:
        while True:
            async with database.sessionmaker() as db:
                rows = (
                    await db.execute(
                        select(MediaPassage, MediaEntry.title, MediaEntry.title_en)
                        .join(MediaEntry, MediaEntry.id == MediaPassage.entry_id)
                        .where(
                            MediaPassage.owner_id == owner,
                            MediaEntry.owner_id == owner,
                            or_(
                                MediaPassage.embedding.is_(None),
                                MediaPassage.embedding_model != model,
                            ),
                        )
                        .order_by(case((MediaEntry.kind == "movie", 0), else_=1), MediaPassage.id)
                        .limit(128)
                        .with_for_update(of=MediaPassage, skip_locked=True)
                    )
                ).all()
                if not rows:
                    print(json.dumps({"done": True, "total": total}), flush=True)
                    break
                texts = [
                    " / ".join(filter(None, [title, english]))[:250] + "\n" + p.body[:2200]
                    for p, title, english in rows
                ]
                print(json.dumps({"model": model, "texts": texts}), flush=True)
                line = await asyncio.to_thread(sys.stdin.readline)
                if not line:
                    break
                response = json.loads(line)
                assert response.get("model") == model
                vectors = response["vectors"]
                assert len(vectors) == len(rows)
                for (passage, _, _), vector in zip(rows, vectors, strict=True):
                    assert len(vector) == 384 and all(math.isfinite(float(v)) for v in vector)
                    passage.embedding, passage.embedding_model = vector, model
                await db.commit()
                total += len(rows)
    finally:
        await database.dispose()


if __name__ == "__main__":
    asyncio.run(main())
