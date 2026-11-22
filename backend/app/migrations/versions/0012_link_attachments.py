"""Pictures and videos of saved pages (a Teswirler pikir, a makalam post),
downloaded into Pikirler as attachments.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-22 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0012'
down_revision: str | None = '0011'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('link_snapshots', sa.Column('media_sources', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('link_snapshots', sa.Column('files_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('link_attachments',
    sa.Column('link_id', sa.UUID(), nullable=False),
    sa.Column('attachment_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['attachment_id'], ['attachments.id'], name=op.f('fk_link_attachments_attachment_id_attachments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_link_attachments_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['link_id', 'owner_id'], ['link_snapshots.id', 'link_snapshots.owner_id'], name='fk_link_attachments_link_owner', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('link_id', 'attachment_id', name=op.f('pk_link_attachments')),
    sa.UniqueConstraint('link_id', 'source_url', name='uq_link_attachments_source'),
    )
    op.create_index('ix_link_attachments_attachment', 'link_attachments', ['attachment_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_link_attachments_attachment', table_name='link_attachments')
    op.drop_table('link_attachments')
    op.drop_column('link_snapshots', 'files_at')
    op.drop_column('link_snapshots', 'media_sources')
