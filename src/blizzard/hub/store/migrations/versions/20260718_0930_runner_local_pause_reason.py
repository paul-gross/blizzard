"""runner local pause reason — the ceiling escalation's cause, made visible (hub store tree)

Phase 5b of epic #57 (issue #61) engages the local pause brake at a spend-ceiling
crossing, composing a reason naming the ceiling and the spend. Before this revision the
hub dropped that string on ingest (``hub/domain/facts.py``), so a ceiling trip was
indistinguishable from a manual ``blizzard runner pause`` on every operator surface. This
adds the column the reason lands in; ``set_by`` already existed but was read only for the
newest-fact-wins boolean, never surfaced — no schema change needed for the cause.

Nullable: a manual pause carries no reason, and every pre-#61 row predates this column —
both read back as ``None``, rendering bare.

Idempotent like ``20260717_2318_hub_chunk_model_selection``: it adds the column only where
an older database created ``runner_local_pause_facts`` without it, so a fresh
``base -> head`` and an in-place upgrade both land at exactly one column.

Revision ID: 20260718_0930_hub_runner_local_pause_reason
Revises: 20260717_2330_hub_usage_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260718_0930_hub_runner_local_pause_reason"
down_revision: str | None = "20260717_2330_hub_usage_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "runner_local_pause_facts"
_COLUMN = "reason"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
