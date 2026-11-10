"""Saved pages: kind (article/post), author username, edit date, comments.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10 20:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('link_snapshots', sa.Column('kind', sa.String(length=16), server_default='article', nullable=False))
    op.add_column('link_snapshots', sa.Column('author_username', sa.String(length=200), nullable=True))
    op.add_column('link_snapshots', sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('link_snapshots', sa.Column('comments', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('link_snapshots', sa.Column('comment_count', sa.Integer(), nullable=True))


def downgrade() -> None:
    for column in ('comment_count', 'comments', 'edited_at', 'author_username', 'kind'):
        op.drop_column('link_snapshots', column)
