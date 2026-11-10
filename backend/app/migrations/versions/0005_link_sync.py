"""Saved pages kept in step with the site: sync time, deleted original, deleted comments.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10 22:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('link_snapshots', sa.Column('deleted_comment_count', sa.Integer(), nullable=True))
    op.add_column('link_snapshots', sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('link_snapshots', sa.Column('source_deleted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for column in ('source_deleted_at', 'synced_at', 'deleted_comment_count'):
        op.drop_column('link_snapshots', column)
