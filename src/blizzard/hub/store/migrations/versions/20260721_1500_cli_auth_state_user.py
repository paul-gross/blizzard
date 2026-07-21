"""auth_state.user_id — the CLI code-exchange's owning user (issue #96, hub store tree)

The CLI's PKCE authorization-code flow (`client=cli`) mints a single-use `auth_state`
row (`kind="cli_login"`) at `authorize` time, once a hub session already resolved the
requesting user — the exchange (`POST /api/auth/cli/token`) needs to know which user to
mint a session for when it later consumes that row, and the table carries no such
column yet (every existing `kind` needs none: the provider-login dance's own row is
consumed before any user exists, and #95's runner-federation row never survives past
one request). Nullable: every non-`cli_login` row leaves it unset.

No `ForeignKey` (see `schema.py`'s own note on this column): SQLite refuses to
`ALTER TABLE ... DROP COLUMN` a column that participates in a foreign key constraint
baked into the table's original `CREATE TABLE` — which this one would be on a fresh
`base -> head` build, since `20260721_1200_hub_auth_oauth`'s own `table.create()` reads
the *current* `schema.py` `auth_state` `Table` object, not a frozen point-in-time copy.
Plain `sa.String()` avoids the restriction entirely; the reference is enforced at the
application layer only (`AuthService.exchange_cli_code` already 404/400s a dangling
one — the row simply fails to resolve a user).

Idempotent like `20260721_1400_hub_runner_redirect_uris` — `op.add_column` only where
an older database lacks it, so a fresh `base -> head` and an in-place upgrade both land
at exactly one added column. No table recreation, so no frozen-literal schema copy.

Revision ID: 20260721_1500_hub_cli_auth_state_user
Revises: 20260721_1400_hub_runner_redirect_uris
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_1500_hub_cli_auth_state_user"
down_revision: str | None = "20260721_1400_hub_runner_redirect_uris"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "auth_state"
_COLUMN = "user_id"


def _existing_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _existing_columns(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _existing_columns(bind):
        op.drop_column(_TABLE, _COLUMN)
