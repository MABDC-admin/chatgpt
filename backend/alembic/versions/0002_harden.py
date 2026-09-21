"""Track knowledge-document files on disk so they can be cleaned up.

Uploads wrote the original file to /data/knowledge but never recorded where,
so the bytes were orphaned the moment the request finished and deleting a
document left them behind permanently.

Revision ID: 0002_harden
Revises: 0001_baseline
Create Date: 2026-09-20
"""

from alembic import op

revision = "0002_harden"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS storage_path VARCHAR(512)")


def downgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS storage_path")
