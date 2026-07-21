"""users / identities / sessions — the auth identity spine (issue #91, hub store tree)

The schema every other auth slice (#92-#96) builds on, independent of any login
mechanism: ``users`` (a hub-local account, ``username`` unique, ``email`` nullable and
unique-when-set via the partial index below, ``role`` the coarse
``blizzard.auth_core.Role`` tag), ``identities`` (one row per linked provider identity,
``UNIQUE(provider_name, subject)``), and ``sessions`` (a hashed-id session row with
sliding expiry columns). Parents before children so the FKs from ``identities`` and
``sessions`` resolve.

``table.create(bind, checkfirst=True)`` also emits the ``uq_users_email`` partial
unique index declared alongside the ``users`` table in ``schema.py`` — it is
associated with that table's metadata, so it rides the same ``CREATE`` (mirrors
``20260713_1947_hub_runner_registry``, which creates two brand-new tables at once).

Revision ID: 20260721_1100_hub_auth_identity_spine
Revises: 20260721_1008_hub_graph_node_session_source
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import identities, sessions, users

revision: str = "20260721_1100_hub_auth_identity_spine"
down_revision: str | None = "20260721_1008_hub_graph_node_session_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [users, identities, sessions]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
