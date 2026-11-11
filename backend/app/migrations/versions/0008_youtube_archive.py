"""Reserve tables for a future native YouTube importer.

The current integration reads a separate SQLite snapshot, not these tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-11 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _times() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def _owner(table: str) -> list:
    return [
        sa.Column('owner_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=f'fk_{table}_owner_id_owners', ondelete='CASCADE'),
    ]


def upgrade() -> None:
    op.create_table('youtube_sources',
    sa.Column('id', sa.UUID(), nullable=False),
    *_owner('youtube_sources'),
    sa.Column('credentials', sa.Text(), nullable=False),
    sa.Column('likes_playlist_id', sa.String(length=100), nullable=True),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_full_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    *_times(),
    sa.PrimaryKeyConstraint('id', name='pk_youtube_sources'),
    sa.UniqueConstraint('owner_id', name='uq_youtube_sources_owner'),
    )
    op.create_table('youtube_videos',
    sa.Column('id', sa.UUID(), nullable=False),
    *_owner('youtube_videos'),
    sa.Column('youtube_video_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('channel_id', sa.String(length=64), nullable=True),
    sa.Column('channel_title', sa.Text(), nullable=True),
    sa.Column('thumbnail_url', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('liked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unliked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('is_available', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('category_id', sa.String(length=16), nullable=True),
    sa.Column('topic_categories', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('content_kind', sa.String(length=16), server_default='unknown', nullable=False),
    sa.Column('kind_confidence', sa.String(length=16), nullable=True),
    sa.Column('kind_reason', sa.Text(), nullable=True),
    sa.Column('artist', sa.Text(), nullable=True),
    sa.Column('track_title', sa.Text(), nullable=True),
    *_times(),
    sa.PrimaryKeyConstraint('id', name='pk_youtube_videos'),
    sa.UniqueConstraint('owner_id', 'youtube_video_id', name='uq_youtube_videos_owner_video'),
    )
    op.create_index('ix_youtube_videos_liked', 'youtube_videos', ['owner_id', 'liked_at'])
    op.create_table('youtube_transcripts',
    sa.Column('id', sa.UUID(), nullable=False),
    *_owner('youtube_transcripts'),
    sa.Column('video_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=True),
    sa.Column('language_code', sa.String(length=32), nullable=True),
    sa.Column('language_name', sa.String(length=100), nullable=True),
    sa.Column('is_generated', sa.Boolean(), nullable=True),
    sa.Column('full_text', sa.Text(), nullable=True),
    sa.Column('segments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('segment_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('whisper_failed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    *_times(),
    sa.ForeignKeyConstraint(['video_id'], ['youtube_videos.id'], name='fk_youtube_transcripts_video_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_youtube_transcripts'),
    sa.UniqueConstraint('video_id', name='uq_youtube_transcripts_video'),
    )
    op.create_table('youtube_lyrics',
    sa.Column('id', sa.UUID(), nullable=False),
    *_owner('youtube_lyrics'),
    sa.Column('video_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=True),
    sa.Column('provider_track_id', sa.String(length=64), nullable=True),
    sa.Column('matched_artist', sa.Text(), nullable=True),
    sa.Column('matched_track', sa.Text(), nullable=True),
    sa.Column('matched_duration_seconds', sa.Float(), nullable=True),
    sa.Column('is_synced', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('language_code', sa.String(length=32), nullable=True),
    sa.Column('full_text', sa.Text(), nullable=True),
    sa.Column('lines', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('line_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    *_times(),
    sa.ForeignKeyConstraint(['video_id'], ['youtube_videos.id'], name='fk_youtube_lyrics_video_id', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_youtube_lyrics'),
    sa.UniqueConstraint('video_id', name='uq_youtube_lyrics_video'),
    )


def downgrade() -> None:
    for table in ('youtube_lyrics', 'youtube_transcripts', 'youtube_videos', 'youtube_sources'):
        op.drop_table(table)
