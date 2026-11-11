"""Keep Russian and English movie data and subtitle progress independently.

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cinema_movies",
        sa.Column("localized", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "cinema_movies",
        sa.Column("metadata_status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column(
        "cinema_movies",
        sa.Column(
            "subtitle_languages", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )
    op.execute("""
        UPDATE cinema_movies SET localized = jsonb_build_object('en', jsonb_build_object(
            'title', title, 'description', description, 'genres', genres,
            'cast', "cast", 'director', director, 'runtime', runtime, 'source', 'cinemeta'))
    """)
    op.execute("""
        UPDATE cinema_movies m SET subtitle_languages = (
            SELECT jsonb_object_agg(s.language, 'ready'::text)
            FROM cinema_subtitles s WHERE s.movie_id = m.id
        ) WHERE EXISTS (SELECT 1 FROM cinema_subtitles s WHERE s.movie_id = m.id)
    """)


def downgrade() -> None:
    op.drop_column("cinema_movies", "subtitle_languages")
    op.drop_column("cinema_movies", "metadata_status")
    op.drop_column("cinema_movies", "localized")
