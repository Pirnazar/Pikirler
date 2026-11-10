"""Saved copies of web pages linked from thoughts.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('link_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('final_url', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('site_name', sa.String(length=200), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('author', sa.String(length=300), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('content_text', sa.Text(), nullable=True),
    sa.Column('word_count', sa.Integer(), nullable=True),
    sa.Column('cover_key', sa.Text(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'ready', 'failed')", name=op.f('ck_link_snapshots_status')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_link_snapshots_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_link_snapshots')),
    sa.UniqueConstraint('thought_id', 'url', name='uq_link_snapshots_thought_url')
    )
    op.create_index('ix_link_snapshots_owner', 'link_snapshots', ['owner_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_link_snapshots_owner', table_name='link_snapshots')
    op.drop_table('link_snapshots')
