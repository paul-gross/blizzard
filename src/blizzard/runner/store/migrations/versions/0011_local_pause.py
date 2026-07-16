"""local pause facts — the runner's own brake, distinct from the hub's (runner store tree)

Issue #43 lands the runner's half of the pause control (``PATCH /runner``, D-043 applied
locally): the operator tells *this* runner to stop claiming and it adheres without the
hub knowing or being reachable ([api.md]). Unlike ``hub_control`` (0006), which upserts a
mirror of a hub-owned value, these are locally-minted facts: pause/start facts append and
the flag derives from the newest (D-004/D-039), the same shape as the hub's own
``runner_pause_facts``. Effective paused is the OR of the two brakes.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 0011_runner_local_pause
Revises: 0010_runner_crash_recovery_context
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import local_pause_facts

revision: str = "0011_runner_local_pause"
down_revision: str | None = "0010_runner_crash_recovery_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (local_pause_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
