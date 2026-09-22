"""Persist download link + filename on messages that produced an artifact.

Without these columns the download URL was only visible during the streaming
session; refreshing the chat page or scrolling back to the message showed the
"Here is your PDF." caption with nothing to click. Same bug affected every
artifact turn (PPTX, DOCX, PDF, XLSX) not just the ones I shipped tonight --
PPT had been quietly losing its link on reload too.

Revision ID: 006_message_file_url
Revises: 005_enhance_attachments
Create Date: 2026-09-22
"""

from alembic import op


revision = "006_message_file_url"
down_revision = "005_enhance_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_url VARCHAR(512)"
    )
    op.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS file_name VARCHAR(512)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS file_url")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS file_name")
