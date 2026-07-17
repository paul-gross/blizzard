"""pause parks — the chunk's dormancy on an operator pause, a separate table pair (issue #46)

A deliberate SEPARATE table pair from park_facts/park_resumes (20260713_1801), not a reshape:
``unforwarded_ask`` reads ``asks.c.question_id.not_in(select(park_facts.c.question_id))``
— a nullable ``question_id`` on that table would make SQL's ``x NOT IN (subquery
containing NULL)`` evaluate to NULL for *every* row, silently breaking ask-and-exit
fleet-wide with a green gate. A pause has no natural key (unlike an ask's fresh
``question_id`` per ask), so this pair's openness is timestamp-correlated instead
(``_pause_park_is_open`` in the store adapter), mirroring ``_intent_is_open``.

Two brand-new tables, no reshape of any existing table — so unlike ``3c72085``'s
freeze hazard (which only bites a revision that *creates* a table a later revision
reshapes), ``20260713_1801_asks_and_parks.py`` needs no freeze here. The runner tree declares
no ForeignKeys at all, so the FK-preservation half of that hazard is inapplicable too.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the two new tables, ``checkfirst``
so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260717_0446_runner_pause_parks
Revises: 20260716_1511_runner_local_pause
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import pause_park_resumes, pause_parks

revision: str = "20260717_0446_runner_pause_parks"
down_revision: str | None = "20260716_1511_runner_local_pause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (pause_parks, pause_park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
