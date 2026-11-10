"""Saved videos: their length and the downloaded file.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-11 10:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('link_snapshots', sa.Column('duration_seconds', sa.Integer(), nullable=True))
    op.add_column('link_snapshots', sa.Column('media_attachment_id', sa.UUID(), nullable=True))
    op.add_column('link_snapshots', sa.Column('media_status', sa.String(length=16), nullable=True))
    op.add_column('link_snapshots', sa.Column('media_error', sa.String(length=64), nullable=True))
    op.create_foreign_key(
        'fk_link_snapshots_media_attachment_id_attachments', 'link_snapshots', 'attachments',
        ['media_attachment_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_link_snapshots_media_attachment_id_attachments', 'link_snapshots', type_='foreignkey')
    for column in ('media_error', 'media_status', 'media_attachment_id', 'duration_seconds'):
        op.drop_column('link_snapshots', column)
