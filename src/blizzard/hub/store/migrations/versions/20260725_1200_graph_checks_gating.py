"""graph checks gating — checks_cwd/checks_timeout on nodes, requires_checks on choices (issue #114, hub store tree)

Issue #114 makes a node's ``checks:`` list a real enforced seam: the runner executes it
at worker exit, and a choice may gate its edge on the results. This revision adds the
three additive columns that carry the authored fields:

- ``graph_nodes.checks_cwd`` (String, null) — where the runner runs the node's checks,
  relative to the leased env's binding workdir; null runs them at the env workdir root.
- ``graph_nodes.checks_timeout`` (Integer, null) — the per-check timeout in seconds; null
  accepts the check-runner's own default.
- ``graph_choices.requires_checks`` (Boolean, null) — whether the choice is gated on green
  checks; null/false is ungated, every pre-#114 choice's shape.

Nullable, no backfill (``bzh:sql-portable``): every graph minted before these columns
existed reads them as ``NULL``, which is semantically unchanged — no ``checks_cwd``/
``checks_timeout`` and no gated choice. The schema change alone flips no behavior; the
runner execution + gating land in later phases.

Idempotent like ``20260721_1008_graph_node_session_source``: it adds each column only
where an older database lacks it, so a fresh ``base -> head`` and an in-place upgrade both
land at exactly one of each column.

Revision ID: 20260725_1200_hub_graph_checks_gating
Revises: 20260722_1200_hub_artifact_forge
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_1200_hub_graph_checks_gating"
down_revision: str | None = "20260722_1200_hub_artifact_forge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NODE_COLUMNS: dict[str, sa.Column] = {
    "checks_cwd": sa.Column("checks_cwd", sa.String(), nullable=True),
    "checks_timeout": sa.Column("checks_timeout", sa.Integer(), nullable=True),
}
_CHOICE_COLUMNS: dict[str, sa.Column] = {
    "requires_checks": sa.Column("requires_checks", sa.Boolean(), nullable=True),
}


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "graph_nodes")
    for name, column in _NODE_COLUMNS.items():
        if name not in existing:
            op.add_column("graph_nodes", column)
    existing = _columns(bind, "graph_choices")
    for name, column in _CHOICE_COLUMNS.items():
        if name not in existing:
            op.add_column("graph_choices", column)


def downgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "graph_choices")
    for name in _CHOICE_COLUMNS:
        if name in existing:
            op.drop_column("graph_choices", name)
    existing = _columns(bind, "graph_nodes")
    for name in _NODE_COLUMNS:
        if name in existing:
            op.drop_column("graph_nodes", name)
