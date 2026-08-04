"""chunk default model/effort — replacing #27's ``model`` column (hub store tree)

Issue #144 retires ``Chunk.model``, a knob with no runtime effect: it never reached the
envelope, so the spawn always used the runner's own default while the hub kept offering
editing surfaces for it. In its place a chunk carries ``default_model`` (a prioritized
preference list, same vocabulary a graph's ``sessions:`` declaration uses) and
``default_effort``, which sit between a session declaration and the runner default.

Both columns are **additive and nullable**, with no backfill and none possible: a
pre-#144 chunk expressed no preference in this vocabulary, and NULL is exactly what
"express no preference" means, so a legacy row keeps today's behavior — the runner
default applies. Filling ``default_model`` in from ``chunks.model`` would be worse than
nothing: it would pin a Claude-native name on every historical chunk in a field that
outranks every session declaration omitting ``model:``.

**``chunks.model`` is retained and no longer read.** Nothing reads or writes it, so every
post-#144 row takes its ``server_default`` and the column is meaningful for **pre-#144
rows only**; it is left in place rather than dropped because dropping it would destroy the
one record of what those older chunks ran. Both halves — the retained column and the
absent backfill — are pinned by
``tests/test_pin_hub_api.py::test_chunk_defaults_retains_model_and_backfills_no_default_model``.

Guarded on the columns already being present, like the other in-place column adds in this
tree, so a fresh ``base -> head`` (where ``schema``'s live metadata already declares them)
and an in-place upgrade of a live store both converge.

Revision ID: 20260728_1410_hub_chunk_defaults
Revises: 20260728_1400_hub_graph_sessions
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1410_hub_chunk_defaults"
down_revision: str | None = "20260728_1400_hub_graph_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "chunks"
# Name-and-type pairs rather than `Column` objects: a `Column` is bound to the table it is
# first attached to, so reusing one across `upgrade`/`downgrade` needs a deprecated `copy()`.
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[str]], ...] = (
    ("default_model", sa.Text()),
    ("default_effort", sa.String()),
)


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name, type_ in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name, _type in _COLUMNS:
            if name in present:
                batch.drop_column(name)
