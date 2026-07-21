"""auth_state / auth_facts — the provider-login seam (issue #92, hub store tree)

The login mechanism's own tables, on top of #91's identity spine: ``auth_state`` (a
single-use ``state`` row round-tripped through a provider redirect, decision D5 — also
reused unmodified by #95's hub-as-IdP authorize) and ``auth_facts`` (the append-only,
non-chunk auth/security event log, ``bzh:facts-not-status`` — ``login_failed``/
``sso_refused`` land here in this phase). Neither table carries a foreign key, so
either order is safe; declared in the same order as ``schema.py``.

``table.create(bind, checkfirst=True)`` mirrors ``20260721_1100_hub_auth_identity_spine``'s
own idempotent multi-table create.

Revision ID: 20260721_1200_hub_auth_oauth
Revises: 20260721_1100_hub_auth_identity_spine
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import auth_facts, auth_state

revision: str = "20260721_1200_hub_auth_oauth"
down_revision: str | None = "20260721_1100_hub_auth_identity_spine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [auth_state, auth_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
