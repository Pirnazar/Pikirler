"""Initial Pikirler schema: owners, thoughts, threads, tags, projects, media,
embeddings, jobs. Also installs the thought-chain integrity trigger.

Revision ID: 0001
Revises: 
Create Date: 2026-10-27 15:18:30.690454
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


THOUGHT_PARENT_GUARD = r'''
CREATE OR REPLACE FUNCTION pikirler_thought_parent_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    parent_owner  uuid;
    parent_thread uuid;
    thread_owner  uuid;
    cursor_id     uuid;
    steps         integer := 0;
BEGIN
    IF NEW.thread_id IS NOT NULL THEN
        SELECT owner_id INTO thread_owner FROM threads WHERE id = NEW.thread_id;
        IF thread_owner IS DISTINCT FROM NEW.owner_id THEN
            RAISE EXCEPTION 'thread belongs to another owner' USING ERRCODE = 'check_violation',
                CONSTRAINT = 'thought_thread_owner';
        END IF;
    END IF;

    IF NEW.parent_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT owner_id, thread_id INTO parent_owner, parent_thread
    FROM thoughts WHERE id = NEW.parent_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'parent thought does not exist' USING ERRCODE = 'foreign_key_violation',
            CONSTRAINT = 'thought_parent_exists';
    END IF;
    IF parent_owner <> NEW.owner_id THEN
        RAISE EXCEPTION 'parent thought belongs to another owner' USING ERRCODE = 'check_violation',
            CONSTRAINT = 'thought_parent_owner';
    END IF;
    IF NEW.thread_id IS DISTINCT FROM parent_thread THEN
        RAISE EXCEPTION 'reply must be in its parent''s thread' USING ERRCODE = 'check_violation',
            CONSTRAINT = 'thought_parent_thread';
    END IF;

    -- Walk up the chain: reaching NEW.id again would close a cycle.
    cursor_id := NEW.parent_id;
    WHILE cursor_id IS NOT NULL LOOP
        IF cursor_id = NEW.id THEN
            RAISE EXCEPTION 'thought chain would contain a cycle' USING ERRCODE = 'check_violation',
                CONSTRAINT = 'thought_chain_acyclic';
        END IF;
        steps := steps + 1;
        IF steps > 100000 THEN
            RAISE EXCEPTION 'thought chain too deep' USING ERRCODE = 'check_violation',
                CONSTRAINT = 'thought_chain_acyclic';
        END IF;
        SELECT parent_id INTO cursor_id FROM thoughts WHERE id = cursor_id;
    END LOOP;
    RETURN NEW;
END;
$$;
'''


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table('owners',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('display_name', sa.String(length=200), nullable=True),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('magic_word_hash', sa.Text(), nullable=True),
    sa.Column('timezone', sa.String(length=64), server_default='UTC', nullable=False),
    sa.Column('ai_external_enabled', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('failed_login_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_owners'))
    )
    op.create_index('uq_owners_email_lower', 'owners', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('ai_usage',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('feature', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('is_external', sa.Boolean(), nullable=False),
    sa.Column('context_thoughts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('request_chars', sa.Integer(), server_default='0', nullable=False),
    sa.Column('response_chars', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('latency_ms', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_ai_usage_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_usage'))
    )
    op.create_index('ix_ai_usage_owner_created', 'ai_usage', ['owner_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('attachments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('safe_filename', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=127), nullable=False),
    sa.Column('declared_mime_type', sa.String(length=127), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('file_size', sa.BigInteger(), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('processing_status', sa.String(length=16), server_default='pending_upload', nullable=False),
    sa.Column('processing_error', sa.Text(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('preview_key', sa.String(length=300), nullable=True),
    sa.Column('thumbnail_key', sa.String(length=300), nullable=True),
    sa.Column('media_info', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name=op.f('ck_attachments_checksum_sha256')),
    sa.CheckConstraint("kind IN ('image', 'video', 'audio', 'document', 'other')", name=op.f('ck_attachments_kind')),
    sa.CheckConstraint("processing_status IN ('pending_upload', 'uploaded', 'processing', 'ready', 'failed')", name=op.f('ck_attachments_processing_status')),
    sa.CheckConstraint('file_size >= 0', name=op.f('ck_attachments_file_size_positive')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_attachments_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_attachments')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_attachments_id_owner'),
    sa.UniqueConstraint('storage_key', name=op.f('uq_attachments_storage_key'))
    )
    op.create_index('ix_attachments_owner_created', 'attachments', ['owner_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_attachments_pending', 'attachments', ['processing_status', 'created_at'], unique=False, postgresql_where=sa.text("processing_status = 'pending_upload'"))
    op.create_table('auth_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('csrf_token_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unlocked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_auth_sessions_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_auth_sessions_token_hash'))
    )
    op.create_index('ix_auth_sessions_owner_active', 'auth_sessions', ['owner_id', 'revoked_at'], unique=False)
    op.create_table('jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('dedupe_key', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('max_attempts', sa.Integer(), server_default='5', nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('run_after', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('enqueued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'running', 'succeeded', 'failed', 'dead')", name=op.f('ck_jobs_status')),
    sa.CheckConstraint('attempts >= 0 AND max_attempts >= 1', name=op.f('ck_jobs_attempts')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_jobs_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs'))
    )
    op.create_index('ix_jobs_due', 'jobs', ['status', 'run_after'], unique=False, postgresql_where=sa.text("status IN ('pending', 'failed', 'running')"))
    op.create_index('ix_jobs_owner', 'jobs', ['owner_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('uq_jobs_active_dedupe', 'jobs', ['dedupe_key'], unique=True, postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('pending', 'running')"))
    op.create_table('projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
    sa.Column('external_ref', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('active', 'paused', 'completed', 'archived')", name=op.f('ck_projects_status')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_projects_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_projects_id_owner')
    )
    op.create_index('uq_projects_owner_external_ref', 'projects', ['owner_id', 'external_ref'], unique=True, postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.create_index('uq_projects_owner_name', 'projects', ['owner_id', sa.literal_column('lower(name)')], unique=True)
    op.create_table('service_clients',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', sa.ARRAY(sa.String(length=64)), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_service_clients_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_service_clients')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_service_clients_token_hash'))
    )
    op.create_index('uq_service_clients_owner_name', 'service_clients', ['owner_id', 'name'], unique=True)
    op.create_table('tags',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('normalized_name', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_tags_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tags')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_tags_id_owner'),
    sa.UniqueConstraint('owner_id', 'normalized_name', name='uq_tags_owner_normalized')
    )
    op.create_index('ix_tags_prefix', 'tags', ['owner_id', 'normalized_name'], unique=False, postgresql_ops={'normalized_name': 'text_pattern_ops'})
    op.create_table('threads',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('root_thought_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_activity_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_threads_owner_id_owners'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_threads'))
    )
    op.create_index('ix_threads_owner_activity', 'threads', ['owner_id', sa.literal_column('last_activity_at DESC')], unique=False)
    op.create_table('thoughts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), server_default='', nullable=False),
    sa.Column('content_format', sa.String(length=16), server_default='plain', nullable=False),
    sa.Column('thought_type', sa.String(length=16), server_default='thought', nullable=False),
    sa.Column('visibility', sa.String(length=16), server_default='private', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='published', nullable=False),
    sa.Column('source', sa.String(length=16), server_default='web', nullable=False),
    sa.Column('source_metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('parent_id', sa.UUID(), nullable=True),
    sa.Column('thread_id', sa.UUID(), nullable=True),
    sa.Column('is_bookmarked', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_pinned', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('search_document', sa.Text(), server_default='', nullable=False),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('simple'::regconfig, coalesce(search_document, '')) || to_tsvector('russian'::regconfig, coalesce(search_document, ''))", persisted=True), nullable=False),
    sa.CheckConstraint("content_format IN ('plain', 'markdown')", name=op.f('ck_thoughts_content_format')),
    sa.CheckConstraint("source IN ('web', 'mobile', 'telegram', 'jemshit', 'api', 'import')", name=op.f('ck_thoughts_source')),
    sa.CheckConstraint("status IN ('draft', 'published', 'archived')", name=op.f('ck_thoughts_status')),
    sa.CheckConstraint("thought_type IN ('thought', 'idea', 'reflection', 'journal', 'project')", name=op.f('ck_thoughts_thought_type')),
    sa.CheckConstraint("visibility IN ('private')", name=op.f('ck_thoughts_visibility_private')),
    sa.CheckConstraint('parent_id IS NULL OR parent_id <> id', name=op.f('ck_thoughts_not_own_parent')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_thoughts_version_positive')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thoughts_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['thoughts.id'], name=op.f('fk_thoughts_parent_id_thoughts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['thread_id'], ['threads.id'], name=op.f('fk_thoughts_thread_id_threads'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thoughts')),
    sa.UniqueConstraint('id', 'owner_id', name='uq_thoughts_id_owner')
    )
    op.create_index('ix_thoughts_bookmarked', 'thoughts', ['owner_id', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL AND is_bookmarked'))
    op.create_index('ix_thoughts_feed', 'thoughts', ['owner_id', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_thoughts_parent', 'thoughts', ['parent_id'], unique=False)
    op.create_index('ix_thoughts_pinned', 'thoughts', ['owner_id'], unique=False, postgresql_where=sa.text('deleted_at IS NULL AND is_pinned'))
    op.create_index('ix_thoughts_search_trgm', 'thoughts', ['search_document'], unique=False, postgresql_using='gin', postgresql_ops={'search_document': 'gin_trgm_ops'})
    op.create_index('ix_thoughts_search_vector', 'thoughts', ['search_vector'], unique=False, postgresql_using='gin')
    op.create_index('ix_thoughts_status_feed', 'thoughts', ['owner_id', 'status', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_thoughts_thread', 'thoughts', ['thread_id', 'created_at'], unique=False)
    op.create_index('ix_thoughts_trash', 'thoughts', ['owner_id', sa.literal_column('deleted_at DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NOT NULL'))
    op.create_index('ix_thoughts_type_feed', 'thoughts', ['owner_id', 'thought_type', sa.literal_column('created_at DESC'), sa.literal_column('id DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_table('thought_attachments',
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('attachment_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['attachment_id', 'owner_id'], ['attachments.id', 'attachments.owner_id'], name='fk_attachment_id_owner_attachments', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_attachments_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('thought_id', 'attachment_id', name=op.f('pk_thought_attachments'))
    )
    op.create_index('ix_thought_attachments_attachment', 'thought_attachments', ['attachment_id'], unique=False)
    op.create_table('thought_embeddings',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=200), nullable=False),
    sa.Column('dimensions', sa.Integer(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('dimensions > 0 AND vector_dims(embedding) = dimensions', name=op.f('ck_thought_embeddings_dimensions_match')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_embeddings_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thought_embeddings')),
    sa.UniqueConstraint('thought_id', 'model', name='uq_thought_embeddings_thought_model')
    )
    op.create_index('ix_thought_embeddings_owner_model', 'thought_embeddings', ['owner_id', 'model'], unique=False)
    op.create_table('thought_projects',
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_projects_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id', 'owner_id'], ['projects.id', 'projects.owner_id'], name='fk_project_id_owner_projects', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('thought_id', 'project_id', name=op.f('pk_thought_projects'))
    )
    op.create_index('ix_thought_projects_project', 'thought_projects', ['project_id'], unique=False)
    op.create_table('thought_relations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('source_thought_id', sa.UUID(), nullable=False),
    sa.Column('target_thought_id', sa.UUID(), nullable=False),
    sa.Column('relation_type', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.String(length=8), server_default='user', nullable=False),
    sa.Column('status', sa.String(length=16), server_default='confirmed', nullable=False),
    sa.Column('confidence', sa.Numeric(precision=4, scale=3), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("created_by = 'user' OR status <> 'confirmed' OR confirmed_at IS NOT NULL", name=op.f('ck_thought_relations_ai_confirmation_explicit')),
    sa.CheckConstraint("created_by IN ('user', 'ai')", name=op.f('ck_thought_relations_created_by')),
    sa.CheckConstraint("relation_type IN ('related', 'continues', 'contradicts', 'supports', 'inspired_by', 'same_topic')", name=op.f('ck_thought_relations_relation_type')),
    sa.CheckConstraint("status IN ('suggested', 'confirmed', 'rejected')", name=op.f('ck_thought_relations_status')),
    sa.CheckConstraint('confidence IS NULL OR (confidence >= 0 AND confidence <= 1)', name=op.f('ck_thought_relations_confidence_range')),
    sa.CheckConstraint('source_thought_id <> target_thought_id', name=op.f('ck_thought_relations_not_self')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_relations_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_source_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['target_thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_target_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thought_relations')),
    sa.UniqueConstraint('owner_id', 'source_thought_id', 'target_thought_id', 'relation_type', name='uq_thought_relations_edge')
    )
    op.create_index('ix_thought_relations_source', 'thought_relations', ['source_thought_id'], unique=False)
    op.create_index('ix_thought_relations_target', 'thought_relations', ['target_thought_id'], unique=False)
    op.create_table('thought_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('revision_number', sa.Integer(), nullable=False),
    sa.Column('previous_content', sa.Text(), nullable=True),
    sa.Column('new_content', sa.Text(), nullable=False),
    sa.Column('change_source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("change_source IN ('create', 'edit', 'restore', 'import')", name=op.f('ck_thought_revisions_change_source')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_revisions_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_thought_revisions')),
    sa.UniqueConstraint('thought_id', 'revision_number', name='uq_thought_revisions_number')
    )
    op.create_table('thought_tags',
    sa.Column('thought_id', sa.UUID(), nullable=False),
    sa.Column('tag_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('origin', sa.String(length=16), server_default='manual', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("origin IN ('manual', 'hashtag')", name=op.f('ck_thought_tags_origin')),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_thought_tags_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tag_id', 'owner_id'], ['tags.id', 'tags.owner_id'], name='fk_tag_id_owner_tags', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id', 'owner_id'], ['thoughts.id', 'thoughts.owner_id'], name='fk_thought_id_owner_thoughts', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('thought_id', 'tag_id', name=op.f('pk_thought_tags'))
    )
    op.create_index('ix_thought_tags_tag', 'thought_tags', ['tag_id'], unique=False)
    op.create_table('voice_transcriptions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('attachment_id', sa.UUID(), nullable=False),
    sa.Column('thought_id', sa.UUID(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('status', sa.String(length=16), server_default='queued', nullable=False),
    sa.Column('requested_language', sa.String(length=8), nullable=True),
    sa.Column('detected_language', sa.String(length=8), nullable=True),
    sa.Column('provider', sa.String(length=64), nullable=True),
    sa.Column('model', sa.String(length=200), nullable=True),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('edited_text', sa.Text(), nullable=True),
    sa.Column('segments', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("requested_language IS NULL OR requested_language IN ('tk', 'ru', 'en', 'zh')", name=op.f('ck_voice_transcriptions_requested_language')),
    sa.CheckConstraint("status IN ('queued', 'processing', 'completed', 'failed')", name=op.f('ck_voice_transcriptions_status')),
    sa.ForeignKeyConstraint(['attachment_id', 'owner_id'], ['attachments.id', 'attachments.owner_id'], name='fk_attachment_id_owner_attachments', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['owners.id'], name=op.f('fk_voice_transcriptions_owner_id_owners'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['thought_id'], ['thoughts.id'], name=op.f('fk_voice_transcriptions_thought_id_thoughts'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_voice_transcriptions'))
    )
    op.create_index('ix_voice_transcriptions_attachment', 'voice_transcriptions', ['attachment_id'], unique=False)
    op.create_index('ix_voice_transcriptions_owner', 'voice_transcriptions', ['owner_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_voice_transcriptions_thought', 'voice_transcriptions', ['thought_id'], unique=False)
    op.create_foreign_key('fk_threads_root_thought_id_thoughts', 'threads', 'thoughts',
                          ['root_thought_id'], ['id'], ondelete='SET NULL')
    op.execute(THOUGHT_PARENT_GUARD)
    op.execute(
        "CREATE TRIGGER thoughts_parent_guard "
        "BEFORE INSERT OR UPDATE OF parent_id, thread_id, owner_id ON thoughts "
        "FOR EACH ROW EXECUTE FUNCTION pikirler_thought_parent_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS thoughts_parent_guard ON thoughts")
    op.execute("DROP FUNCTION IF EXISTS pikirler_thought_parent_guard()")
    op.drop_constraint('fk_threads_root_thought_id_thoughts', 'threads', type_='foreignkey')
    op.drop_index('ix_voice_transcriptions_thought', table_name='voice_transcriptions')
    op.drop_index('ix_voice_transcriptions_owner', table_name='voice_transcriptions')
    op.drop_index('ix_voice_transcriptions_attachment', table_name='voice_transcriptions')
    op.drop_table('voice_transcriptions')
    op.drop_index('ix_thought_tags_tag', table_name='thought_tags')
    op.drop_table('thought_tags')
    op.drop_table('thought_revisions')
    op.drop_index('ix_thought_relations_target', table_name='thought_relations')
    op.drop_index('ix_thought_relations_source', table_name='thought_relations')
    op.drop_table('thought_relations')
    op.drop_index('ix_thought_projects_project', table_name='thought_projects')
    op.drop_table('thought_projects')
    op.drop_index('ix_thought_embeddings_owner_model', table_name='thought_embeddings')
    op.drop_table('thought_embeddings')
    op.drop_index('ix_thought_attachments_attachment', table_name='thought_attachments')
    op.drop_table('thought_attachments')
    op.drop_index('ix_thoughts_type_feed', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_thoughts_trash', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NOT NULL'))
    op.drop_index('ix_thoughts_thread', table_name='thoughts')
    op.drop_index('ix_thoughts_status_feed', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_thoughts_search_vector', table_name='thoughts', postgresql_using='gin')
    op.drop_index('ix_thoughts_search_trgm', table_name='thoughts', postgresql_using='gin', postgresql_ops={'search_document': 'gin_trgm_ops'})
    op.drop_index('ix_thoughts_pinned', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NULL AND is_pinned'))
    op.drop_index('ix_thoughts_parent', table_name='thoughts')
    op.drop_index('ix_thoughts_feed', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_thoughts_bookmarked', table_name='thoughts', postgresql_where=sa.text('deleted_at IS NULL AND is_bookmarked'))
    op.drop_table('thoughts')
    op.drop_index('ix_threads_owner_activity', table_name='threads')
    op.drop_table('threads')
    op.drop_index('ix_tags_prefix', table_name='tags', postgresql_ops={'normalized_name': 'text_pattern_ops'})
    op.drop_table('tags')
    op.drop_index('uq_service_clients_owner_name', table_name='service_clients')
    op.drop_table('service_clients')
    op.drop_index('uq_projects_owner_name', table_name='projects')
    op.drop_index('uq_projects_owner_external_ref', table_name='projects', postgresql_where=sa.text('external_ref IS NOT NULL'))
    op.drop_table('projects')
    op.drop_index('uq_jobs_active_dedupe', table_name='jobs', postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('pending', 'running')"))
    op.drop_index('ix_jobs_owner', table_name='jobs')
    op.drop_index('ix_jobs_due', table_name='jobs', postgresql_where=sa.text("status IN ('pending', 'failed', 'running')"))
    op.drop_table('jobs')
    op.drop_index('ix_auth_sessions_owner_active', table_name='auth_sessions')
    op.drop_table('auth_sessions')
    op.drop_index('ix_attachments_pending', table_name='attachments', postgresql_where=sa.text("processing_status = 'pending_upload'"))
    op.drop_index('ix_attachments_owner_created', table_name='attachments')
    op.drop_table('attachments')
    op.drop_index('ix_ai_usage_owner_created', table_name='ai_usage')
    op.drop_table('ai_usage')
    op.drop_index('uq_owners_email_lower', table_name='owners')
    op.drop_table('owners')
