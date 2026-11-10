"""One saved copy per page, shared by every thought that links to it; "new
comments" marker; AI summary of a saved page; time of the last video download.

Existing copies of the same page are merged: the most complete one is kept
(ready before failed, then most comments, then the latest fetch), every
thought is linked to it, the others are removed (their cover files through
the outbox; a downloaded video of a removed copy becomes an orphan that
housekeeping deletes).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-11 14:00:00
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.modules.pikirler.services.link_keys import canonical_url

revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('link_snapshots', sa.Column('canonical_url', sa.Text(), nullable=True))
    op.add_column('link_snapshots', sa.Column('comments_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('link_snapshots', sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('link_snapshots', sa.Column('summary_language', sa.String(length=8), nullable=True))
    op.add_column('link_snapshots', sa.Column('summary_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('link_snapshots', sa.Column('media_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE link_snapshots SET media_at = updated_at WHERE media_status IN ('ready', 'failed')")
    op.create_table('thought_links',
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('link_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_links_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('thought_id', 'link_id', name=op.f('pk_thought_links'))
    )
    op.create_index('ix_thought_links_link', 'thought_links', ['link_id'], unique=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, owner_id, thought_id, url, position, status, comment_count, fetched_at, "
        "cover_key FROM link_snapshots"
    )).mappings().all()
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((str(r['owner_id']), canonical_url(r['url'])), []).append(dict(r))
    drop: list[str] = []
    covers: dict[str, list[str]] = {}
    for (owner_id, key), items in groups.items():
        items.sort(key=lambda r: (
            r['status'] != 'ready',
            -(r['comment_count'] or 0),
            -(r['fetched_at'].timestamp() if r['fetched_at'] else 0),
        ))
        keep = items[0]
        conn.execute(sa.text("UPDATE link_snapshots SET canonical_url = :k WHERE id = :id"),
                     {"k": key, "id": keep['id']})
        for r in items:
            conn.execute(sa.text(
                "INSERT INTO thought_links (thought_id, link_id, owner_id, url, position) "
                "VALUES (:t, :l, :o, :u, :p) ON CONFLICT DO NOTHING"
            ), {"t": r['thought_id'], "l": keep['id'], "o": r['owner_id'], "u": r['url'], "p": r['position']})
            if r is not keep:
                drop.append(str(r['id']))
                if r['cover_key'] and r['cover_key'] != keep['cover_key']:
                    covers.setdefault(owner_id, []).append(r['cover_key'])
    if drop:
        conn.execute(sa.text("DELETE FROM link_snapshots WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": drop})
    for owner_id, keys in covers.items():
        conn.execute(sa.text(
            "INSERT INTO jobs (id, owner_id, kind, payload, max_attempts) "
            "VALUES (:id, :o, 'delete_link_covers', CAST(:p AS jsonb), 5)"
        ), {"id": str(uuid.uuid4()), "o": owner_id, "p": json.dumps({"keys": keys})})
    # Comments saved so far count as read.
    conn.execute(sa.text("UPDATE link_snapshots SET comments_seen_at = now()"))

    op.drop_constraint('uq_link_snapshots_thought_url', 'link_snapshots', type_='unique')
    op.drop_constraint('fk_thought_id_owner_thoughts', 'link_snapshots', type_='foreignkey')
    op.drop_column('link_snapshots', 'thought_id')
    op.drop_column('link_snapshots', 'position')
    op.alter_column('link_snapshots', 'canonical_url', nullable=False)
    op.create_unique_constraint('uq_link_snapshots_owner_page', 'link_snapshots', ['owner_id', 'canonical_url'])
    op.create_unique_constraint('uq_link_snapshots_id_owner', 'link_snapshots', ['id', 'owner_id'])
    op.create_foreign_key(
        'fk_link_id_owner_link_snapshots', 'thought_links', 'link_snapshots',
        ['link_id', 'owner_id'], ['id', 'owner_id'], ondelete='CASCADE',
    )


def downgrade() -> None:
    raise NotImplementedError("Copies were merged; restore a backup to go back.")
