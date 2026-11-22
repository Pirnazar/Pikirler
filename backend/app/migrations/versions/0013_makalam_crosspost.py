"""makalam.com as a second place a thought can be published.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23 12:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(table: str, name: str, column: str, values: str) -> None:
    op.drop_constraint(op.f(f'ck_{table}_{name}'), table, type_='check')
    op.create_check_constraint(op.f(f'ck_{table}_{name}'), table, f"{column} IN ({values})")


def upgrade() -> None:
    _replace('external_accounts', 'provider', 'provider', "'teswirler', 'makalam'")
    _replace('crossposts', 'target', 'target', "'teswirler', 'makalam'")


def downgrade() -> None:
    op.execute("DELETE FROM crossposts WHERE target = 'makalam'")
    op.execute("DELETE FROM external_accounts WHERE provider = 'makalam'")
    _replace('crossposts', 'target', 'target', "'teswirler'")
    _replace('external_accounts', 'provider', 'provider', "'teswirler'")
