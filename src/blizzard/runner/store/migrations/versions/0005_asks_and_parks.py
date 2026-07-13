"""asks and park/resume fact tables (runner store tree)

P7 lands the ask/answer protocol's machine-local facts ([ask-answer.md]): ``asks``
(the worker's local open-ask fact ``blizzard runner ask`` records before the worker
exits — how ADVANCE tells a park from a verdict-less death, D-009), and
``park_facts``/``park_resumes`` (the chunk's dormancy on a question and the
resume-with-answer that ends it). Before this the runner had no home for an ask, so a
parked worker's exit was indistinguishable from a crash.

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the three new tables,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 0005_runner_asks_and_parks
Revises: 0004_runner_heartbeats
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import asks, park_facts, park_resumes

revision: str = "0005_runner_asks_and_parks"
down_revision: str | None = "0004_runner_heartbeats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (asks, park_facts, park_resumes)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
