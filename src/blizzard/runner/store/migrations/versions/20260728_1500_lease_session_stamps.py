"""lease_context session stamps — the session name, model, and effort (runner store tree)

Issue #144 gives a lease three new facts about the session it ran: the **declared pool**
it belongs to (``session_name``), and the **resolved** model and effort it actually ran
under. Three columns on ``lease_context``, which is already written exactly once per lease
at mint — so the stamp rides that same single insert and opens no new crash window.

They are what makes takeover and escalation a *read* rather than a re-resolution: an
operator continues under exactly the configuration the session ran with, instead of
whatever a fresh resolution would produce now. ``resolved_model`` is also what the
rotation check compares against the pool's currently-resolved model, and what the usage
fallback attributes a resume-path invocation's spend to.

**Nullable, with no backfill.** A lease minted before this revision reads NULL on all
three, which means *unknown* — not "no pool" and not "the runner default". Both consumers
decline to guess: takeover renders today's bare resume command, and the usage fallback
attributes nothing. Backfilling would be fabrication — those sessions ran under whatever
the runner's single pinned model was at the time, which this store never recorded.

Guarded on the columns already being present, like the other in-place column adds in this
tree, so a fresh ``base -> head`` (where ``schema``'s live metadata already declares them)
and an in-place upgrade of a live store both converge.

Revision ID: 20260728_1500_runner_lease_session_stamps
Revises: 20260727_1000_runner_session_preamble_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_1500_runner_lease_session_stamps"
down_revision: str | None = "20260727_1000_runner_session_preamble_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "lease_context"
_COLUMNS = ("session_name", "resolved_model", "resolved_effort")


def _columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    for name in _COLUMNS:
        if name not in present:
            op.add_column(_TABLE, sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind)
    with op.batch_alter_table(_TABLE) as batch:
        for name in _COLUMNS:
            if name in present:
                batch.drop_column(name)
