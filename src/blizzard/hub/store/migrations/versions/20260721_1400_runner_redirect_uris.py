"""runner federation registration: public_url + redirect_uris on the registry (hub store tree)

Issue #95 has a runner optionally register its own browser-reachable base URL and the
redirect URIs the hub's IdP authorize endpoint is allowed to bounce a browser to — the
open-redirect guard is an exact match against this set. This revision adds the two
columns those land in.

Nullable/JSON-encoded `redirect_uris` (a `Text` column carrying `json.dumps(list[str])`):
a runner registered by a client that predates this field reports neither, and cannot be
an IdP-authorize target (`GET /api/auth/authorize` 400s on an unregistered client rather
than guessing a redirect). A rotating pair, not an append-only fact
(``bzh:facts-not-status``'s one deliberate exception — the registration row is already a
mutable upsert; see ``hub/domain/registry.py``'s module docstring): a re-registration
(the runner's heartbeat) overwrites both in place, so an operator changing the runner's
public URL converges on the next pull.

Idempotent like ``20260718_1300_hub_runner_env_capacity``: each column is added only
where an older database lacks it, so a fresh ``base -> head`` and an in-place upgrade
both land at exactly the two columns. ``op.add_column`` on the existing table — no table
recreation, so no frozen-literal schema copy is needed.

Revision ID: 20260721_1400_hub_runner_redirect_uris
Revises: 20260721_1300_hub_auth_superuser_bootstrap
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1400_hub_runner_redirect_uris"
down_revision: str | None = "20260721_1300_hub_auth_superuser_bootstrap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMNS = ("public_url", "redirect_uris")


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column not in existing:
            op.add_column(_TABLE, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_columns(bind)
    for column in _COLUMNS:
        if column in existing:
            op.drop_column(_TABLE, column)
