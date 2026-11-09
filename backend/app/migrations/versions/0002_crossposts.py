"""Cross-posting: external accounts (encrypted sessions) and per-thought copies.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('external_accounts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('remote_user_id', sa.String(length=200), nullable=True),
    sa.Column('username', sa.String(length=200), nullable=True),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('credentials', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("provider IN ('teswirler')", name=op.f('ck_external_accounts_provider')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_external_accounts_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_accounts')),
    sa.UniqueConstraint('owner_id', 'provider', name='uq_external_accounts_owner_provider')
    )
    op.create_table('crossposts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('target', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('remote_id', sa.String(length=200), nullable=True),
    sa.Column('remote_url', sa.Text(), nullable=True),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=300), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed', 'unknown')", name=op.f('ck_crossposts_status')),
    sa.CheckConstraint("target IN ('teswirler')", name=op.f('ck_crossposts_target')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_crossposts_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_crossposts')),
    sa.UniqueConstraint('thought_id', 'target', name='uq_crossposts_thought_target')
    )
    op.create_index('ix_crossposts_owner', 'crossposts', ['owner_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_crossposts_owner', table_name='crossposts')
    op.drop_table('crossposts')
    op.drop_table('external_accounts')
