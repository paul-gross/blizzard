"""app credentials — a credential may name the ``github.app-installation`` binding.

The upgrade changes no table: a credential's ``binding`` and ``settings`` already hold any binding's name and settings.
The downgrade is refused, naming every App credential by tenant and slug, while one exists — a hub before this revision
cannot act with one. It writes nothing.
Revision ID: 20261114_0900_app_credentials
Revises: 20261113_1000_credential_carry_over
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0900_app_credentials"
down_revision: str | None = "20261113_1000_credential_carry_over"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_BINDING = "github.app-installation"

# The tables this revision reads, frozen to the columns it names (``bzh:frozen-revisions``).
_tenants = sa.table("tenants", sa.column("tenant_id", sa.String), sa.column("name", sa.String))
_credentials = sa.table(
    "credentials",
    sa.column("tenant_id", sa.String),
    sa.column("slug", sa.String),
    sa.column("binding", sa.String),
)


def upgrade() -> None:
    """Nothing to change: the binding is new to the hub, not to the store's shape."""


def downgrade() -> None:
    bind = op.get_bind()
    names = {str(row.tenant_id): str(row.name) for row in bind.execute(sa.select(_tenants.c.tenant_id, _tenants.c.name))}
    rows = bind.execute(
        sa.select(_credentials.c.tenant_id, _credentials.c.slug)
        .where(_credentials.c.binding == _APP_BINDING)
        .order_by(_credentials.c.tenant_id, _credentials.c.slug)
    )
    refused = [f"tenant {names.get(t, t)} ({t}): credential {slug}" for t, slug in rows]
    if refused:
        raise RuntimeError(
            f"a hub before this revision cannot act with a {_APP_BINDING} credential: "
            + "; ".join(refused)
            + " — retire and delete each credential named, then downgrade again"
        )
