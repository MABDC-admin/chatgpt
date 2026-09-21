"""Enhance attachments for file management

Revision ID: 005
Revises: 004
Create Date: 2026-09-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "005_enhance_attachments"
down_revision = "004_add_user_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add new columns to attachments table
    op.add_column("attachments", sa.Column("source_type", sa.String(16), nullable=False, server_default="uploaded"))
    op.add_column("attachments", sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True))
    op.add_column("attachments", sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True))
    op.add_column("attachments", sa.Column("thumbnail_path", sa.String(512), nullable=True))
    op.add_column("attachments", sa.Column("original_filename", sa.String(512), nullable=True))

    # Backfill original_filename from existing filename column
    op.execute("UPDATE attachments SET original_filename = filename WHERE original_filename IS NULL")

    # Create index for faster user file queries
    op.create_index("ix_attachments_user_created", "attachments", ["user_id", sa.text("created_at DESC")])


def downgrade() -> None:
    op.drop_index("ix_attachments_user_created", table_name="attachments")
    op.drop_column("attachments", "original_filename")
    op.drop_column("attachments", "thumbnail_path")
    op.drop_column("attachments", "message_id")
    op.drop_column("attachments", "conversation_id")
    op.drop_column("attachments", "source_type")