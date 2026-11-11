"""Unified media corpus, local semantic vectors, citations and movie lists."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("watch_status", sa.String(16), nullable=False, server_default="unlisted"),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("user_rating", sa.Integer()),
        sa.Column("user_comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("watched_at", sa.DateTime(timezone=True)),
        sa.Column("preferences_version", sa.Integer(), nullable=False, server_default="1"),
    ):
        op.add_column("cinema_movies", column)
    op.create_check_constraint(
        "watch_status", "cinema_movies", "watch_status IN ('unlisted', 'want', 'watched')"
    )
    op.create_check_constraint(
        "user_rating", "cinema_movies", "user_rating IS NULL OR user_rating BETWEEN 1 AND 10"
    )
    op.create_table(
        "media_entries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("movie_id", sa.UUID(), sa.ForeignKey("cinema_movies.id", ondelete="CASCADE")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("title_en", sa.Text()),
        sa.Column("creator", sa.Text()),
        sa.Column("year", sa.Integer()),
        sa.Column("genres", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("poster", sa.Text()),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("owner_id", "source_key"),
        sa.UniqueConstraint("id", "owner_id"),
    )
    op.create_index(
        "ix_media_entries_owner_kind_year", "media_entries", ["owner_id", "kind", "year"]
    )
    op.create_index("ix_media_entries_genres", "media_entries", ["genres"], postgresql_using="gin")
    op.create_table(
        "media_passages",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.UUID(), nullable=False),
        sa.Column("source_key", sa.String(128), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("part", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("segments", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("start_ms", sa.Integer()),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("search_document", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple'::regconfig, search_document) || to_tsvector('russian'::regconfig, search_document) || to_tsvector('english'::regconfig, search_document)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(384)),
        sa.Column("embedding_model", sa.String(100)),
        sa.ForeignKeyConstraint(
            ["entry_id", "owner_id"],
            ["media_entries.id", "media_entries.owner_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("entry_id", "source_key"),
    )
    op.create_index("ix_media_passages_owner_language", "media_passages", ["owner_id", "language"])
    op.create_index("ix_media_passages_entry", "media_passages", ["entry_id"])
    op.create_index(
        "ix_media_passages_search", "media_passages", ["search_vector"], postgresql_using="gin"
    )
    op.create_table(
        "media_index_states",
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("owners.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("archive_stamp", sa.String(100)),
        sa.Column("phase", sa.String(16), nullable=False, server_default="empty"),
        sa.Column("error", sa.String(200)),
        sa.Column("processed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "media_saved_quotes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "owner_id", sa.UUID(), sa.ForeignKey("owners.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "thought_id",
            sa.UUID(),
            sa.ForeignKey("thoughts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quote_key", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("owner_id", "quote_key"),
    )


def downgrade():
    for name in ("media_saved_quotes", "media_index_states", "media_passages", "media_entries"):
        op.drop_table(name)
    op.drop_constraint(op.f("ck_cinema_movies_watch_status"), "cinema_movies", type_="check")
    op.drop_constraint(op.f("ck_cinema_movies_user_rating"), "cinema_movies", type_="check")
    for name in (
        "preferences_version",
        "watched_at",
        "user_comment",
        "user_rating",
        "is_favorite",
        "watch_status",
    ):
        op.drop_column("cinema_movies", name)
