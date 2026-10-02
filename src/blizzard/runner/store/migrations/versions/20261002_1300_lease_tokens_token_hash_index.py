"""An index on ``lease_tokens.token_hash`` for resolving a lease from the token alone (runner store tree).

Revision ID: 20261002_1300_runner_lease_tokens_token_hash_index
Revises: 20261002_1200_runner_trace_latch_kind_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_1300_runner_lease_tokens_token_hash_index"
down_revision: str | None = "20261002_1200_runner_trace_latch_kind_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_tokens"
_INDEX = "ix_lease_tokens_token_hash"


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, ["token_hash"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
