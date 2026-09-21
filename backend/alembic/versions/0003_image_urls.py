"""Repoint stored image URLs at the authenticated media route.

Images used to be served by a public StaticFiles mount at /media/images/,
which exposed every generated picture to anyone holding the link. That mount
is gone and images now go through GET /api/images/file/{filename}, so rows
written before the change would otherwise point at a route that no longer
exists. The frontend carries a fallback for these, but data should not depend
on a client-side shim indefinitely.

Revision ID: 0003_image_urls
Revises: 0002_harden
Create Date: 2026-09-20
"""

from alembic import op

revision = "0003_image_urls"
down_revision = "0002_harden"
branch_labels = None
depends_on = None

_OLD = "/media/images/"
_NEW = "/api/images/file/"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE messages
           SET image_url = replace(image_url, '{_OLD}', '{_NEW}')
         WHERE image_url LIKE '{_OLD}%'
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE messages
           SET image_url = replace(image_url, '{_NEW}', '{_OLD}')
         WHERE image_url LIKE '{_NEW}%'
        """
    )
