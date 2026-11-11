"""Movie library and searchable, timestamped subtitles.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cinema_movies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("imdb_id", sa.String(16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.String(16)),
        sa.Column("poster", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("runtime", sa.String(80)),
        sa.Column("rating", sa.String(16)),
        *[
            sa.Column(name, pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False)
            for name in ("genres", "cast", "director", "aliases")
        ],
        sa.Column("subtitle_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("subtitle_error", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("owner_id", "imdb_id"),
    )
    op.create_index("ix_cinema_movies_owner", "cinema_movies", ["owner_id"])
    op.create_table(
        "cinema_subtitles",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "movie_id",
            sa.UUID(),
            sa.ForeignKey("cinema_movies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("cue_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("movie_id", "source", "provider_id"),
    )
    op.create_index("ix_cinema_subtitles_movie_id", "cinema_subtitles", ["movie_id"])
    op.create_table(
        "cinema_cues",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "subtitle_id",
            sa.UUID(),
            sa.ForeignKey("cinema_subtitles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            pg.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, body) || to_tsvector('russian'::regconfig, body) || to_tsvector('english'::regconfig, body)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.UniqueConstraint("subtitle_id", "position"),
        sa.CheckConstraint("start_ms >= 0 AND end_ms >= start_ms", name="cue_times"),
    )
    op.create_index("ix_cinema_cues_subtitle_id", "cinema_cues", ["subtitle_id"])
    op.create_index(
        "ix_cinema_cues_search", "cinema_cues", ["search_vector"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_table("cinema_cues")
    op.drop_table("cinema_subtitles")
    op.drop_table("cinema_movies")
