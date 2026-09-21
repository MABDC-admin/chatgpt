"""Baseline: adopt the create_all schema and formalise ad-hoc column patches.

Tables themselves are created by `Base.metadata.create_all` at startup, which
is a no-op for tables that already exist. Alembic owns *alterations* from here
on. Every statement below is idempotent so it is safe to run against both a
freshly created database and the live one that predates migrations.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-20
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Was applied by hand in app/main.py before migrations existed.
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS image_url VARCHAR(512)")

    # Conversation list is filtered on archived and ordered by updated_at on
    # every page load; the composite covers that access path.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversations_user_archived_updated "
        "ON conversations (user_id, archived, updated_at DESC)"
    )

    # Title search uses ILIKE '%term%', which no btree index can serve.
    # pg_trgm gives a GIN index that does.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversations_title_trgm "
        "ON conversations USING gin (title gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_conversations_title_trgm")
    op.execute("DROP INDEX IF EXISTS ix_conversations_user_archived_updated")
    # image_url is left in place: dropping it would destroy chat history.
