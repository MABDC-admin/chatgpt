"""add user status column

Revision ID: 004_add_user_status
Revises: 003_attachments
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "004_add_user_status"
down_revision = "0003_image_urls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add status column as nullable first so existing rows get NULL
    op.add_column(
        "users",
        sa.Column("status", sa.String(16), nullable=True),
    )
    # Backfill all existing users to 'active'
    op.execute("UPDATE users SET status = 'active' WHERE status IS NULL")
    # Now enforce NOT NULL and set the server default for future inserts
    op.alter_column("users", "status", nullable=False, server_default="active")
    # Create index for faster filtering of pending users
    op.create_index("ix_users_status", "users", ["status"])


def downgrade() -> None:
    op.drop_index("ix_users_status", table_name="users")
    op.drop_column("users", "status")
