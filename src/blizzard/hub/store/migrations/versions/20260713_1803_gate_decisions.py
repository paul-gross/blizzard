"""human-gate decisions, resolutions, and requeue facts (hub store tree)

The P7W2 human-loop tables (design/domain/work.md Decision, D-045/D-032/D-067):

* ``decisions`` — a gate's open parking row: the node, its choice set, and the
  parked step's epoch. Written by the hub when a transition lands on a human-judged
  node (a *graph* gate) or by the runner in place of a transition for a node it was
  configured to gate (a *runner-config* gate).
* ``decision_resolutions`` — the person's picked choice, first-write-wins on the
  decision_id primary key (D-045, like an answer).
* ``requeues`` — closes an open escalation by supersession (``blizzard hub requeue``,
  D-067), never a resolution fact.

The tables are defined once in ``blizzard.hub.store.schema`` (the metadata Alembic
targets); this revision creates exactly its own subset in FK-dependency order, so a
fresh ``base -> head`` and an in-place upgrade of a pre-P7W2 store both land here.

Revision ID: 20260713_1803_hub_gate_decisions
Revises: 20260713_1801_hub_questions_and_answers
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import decision_resolutions, decisions, requeues

revision: str = "20260713_1803_hub_gate_decisions"
down_revision: str | None = "20260713_1801_hub_questions_and_answers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Parents before children so the FK constraints resolve.
_TABLES = [decisions, decision_resolutions, requeues]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
