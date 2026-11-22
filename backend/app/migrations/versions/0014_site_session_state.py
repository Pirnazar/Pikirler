"""Keep the sessions on other sites alive and notice when one ends.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-23 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('external_accounts', sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('external_accounts', sa.Column('expired_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('external_accounts', 'expired_at')
    op.drop_column('external_accounts', 'checked_at')
