"""chunk model selection — a mutable ``model`` column on chunks (issue #27, hub store tree)

Ingest now pins a chunk to a model at mint, editable later (alongside ``graph_id``)
while the chunk rests ``not_ready`` (``domain/edit.py``). ``model`` is a plain mutable
column, not an append-only fact table — the same shape ``graph_id`` already carries.

Every chunk already in flight predates this column; ``server_default`` backfills every
existing row to the model the fleet actually ran on before this field existed (mirrors
the runner's own fixed adapter constant, ``DEFAULT_WORKER_MODEL`` in
``blizzard.runner.harness.internal.claude_code_adapter``). A local literal, not an
import (``bzh:frozen-revisions``): this revision's backfill must not move if the hub
domain's own ``DEFAULT_MODEL`` ever does.

Idempotent like ``20260713_1424_hub_escalation_takeover``: it adds the column only where
an older database created ``chunks`` without it, so a fresh ``base -> head`` and an
in-place upgrade both land at exactly one column.

Revision ID: 20260717_2318_hub_chunk_model_selection
Revises: 20260717_0446_hub_chunk_pause_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260717_2318_hub_chunk_model_selection"
down_revision: str | None = "20260717_0446_hub_chunk_pause_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
_COLUMN = "model"

# The model every chunk ran on before this column existed — see the module docstring.
_DEFAULT_MODEL = "claude-opus-4-8"


def _has_column(bind: sa.Connection) -> bool:
    return _COLUMN in {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=False, server_default=_DEFAULT_MODEL))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column(_TABLE, _COLUMN)
