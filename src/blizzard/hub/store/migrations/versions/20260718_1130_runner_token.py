"""hub-minted runner bearer tokens: token_hash + its lookup index (hub store tree)

Issue #86a lands the hub-side half of runner authentication: an operator-enrolled,
hub-minted per-runner bearer token, presented on every runner->hub call. Only the
token's sha256 hex digest is ever persisted — this revision adds the column it lands
in, plus an index, because the auth dependency's resolution direction is the reverse
of every other registry lookup: a presented token resolves to *its* runner
(``registration_for_token_hash``), not a runner_id to its row, so the lookup needs to
be indexed on the hash rather than the (already-primary-keyed) ``runner_id``.

Nullable: an unenrolled runner (every runner before its first ``enroll`` call, and
every pre-#86a row) has none. A rotating column, not an append-only fact
(``bzh:facts-not-status``'s one deliberate exception — see ``hub/domain/registry.py``'s
module docstring): the registration row is already a mutable upsert (``last_seen_at``,
``workspace_id`` rewrite in place), so a rotating hash column is consistent with the
rest of the row, unlike the route capability token (#84's append-only fact table).

Idempotent like ``20260718_0930_hub_runner_local_pause_reason``: the column (and its
index) are added only where an older database lacks them, so a fresh ``base -> head``
and an in-place upgrade both land at exactly one column and one index.

Revision ID: 20260718_1130_hub_runner_token
Revises: 20260718_0030_hub_node_poll
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_1130_hub_runner_token"
down_revision: str | None = "20260718_0030_hub_node_poll"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_registrations"
_COLUMN = "token_hash"
_INDEX = "ix_runner_registrations_token_hash"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def _has_index(bind: sa.Connection) -> bool:
    return _INDEX in {i["name"] for i in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))
    if not _has_index(bind):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
