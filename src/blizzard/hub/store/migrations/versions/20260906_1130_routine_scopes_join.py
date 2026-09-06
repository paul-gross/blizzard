"""``routine_scopes`` — the declared many-to-many between a routine and every scope its
runs may write into (blizzard#488), seeded from history: every ``(routine_name,
scope_slug)`` pair a routine's own ``findings``/``finding_sets`` have ever carried, plus
each routine's own ``default_scope_slug``.

Revision ID: 20260906_1130_routine_scopes_join
Revises: 20260905_1100_hub_runner_external_usage_slug
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import insert, select

revision: str = "20260906_1130_routine_scopes_join"
down_revision: str | None = "20260905_1100_hub_runner_external_usage_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# This revision's own frozen shape (``bzh:frozen-revisions``). ``scopes``/``routines``
# below are FK-resolution stubs — never created, never dropped; ``routines`` is widened
# past that bare need to the columns the seed below reads. ``findings``/``finding_sets``
# are narrow read-only stubs: this revision never creates or drops either.
_frozen_metadata = sa.MetaData()

_scopes = sa.Table(
    "scopes",
    _frozen_metadata,
    sa.Column("slug", sa.String, primary_key=True),
)
_routines = sa.Table(
    "routines",
    _frozen_metadata,
    sa.Column("routine_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("default_scope_slug", sa.String, nullable=False),
)
_findings = sa.Table(
    "findings",
    _frozen_metadata,
    sa.Column("finding_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
)
_finding_sets = sa.Table(
    "finding_sets",
    _frozen_metadata,
    sa.Column("finding_set_id", sa.String, primary_key=True),
    sa.Column("routine_name", sa.String, nullable=False),
    sa.Column("scope_slug", sa.String, nullable=False),
)
_routine_scopes = sa.Table(
    "routine_scopes",
    _frozen_metadata,
    sa.Column("routine_id", sa.String, sa.ForeignKey("routines.routine_id"), primary_key=True),
    sa.Column("scope_slug", sa.String, sa.ForeignKey("scopes.slug"), primary_key=True),
)
sa.Index("ix_routine_scopes_scope_slug", _routine_scopes.c.scope_slug)


def upgrade() -> None:
    bind = op.get_bind()
    _routine_scopes.create(bind, checkfirst=True)

    routine_id_by_name: dict[str, str] = {}
    default_scope_by_routine: dict[str, str] = {}
    for r in bind.execute(select(_routines.c.routine_id, _routines.c.name, _routines.c.default_scope_slug)):
        routine_id_by_name[r.name] = r.routine_id
        default_scope_by_routine[r.routine_id] = r.default_scope_slug

    pairs: set[tuple[str, str]] = set()
    for r in bind.execute(select(_findings.c.routine_name, _findings.c.scope_slug)):
        routine_id = routine_id_by_name.get(r.routine_name)
        if routine_id is not None:
            pairs.add((routine_id, r.scope_slug))
    for r in bind.execute(select(_finding_sets.c.routine_name, _finding_sets.c.scope_slug)):
        if not r.routine_name:
            continue
        routine_id = routine_id_by_name.get(r.routine_name)
        if routine_id is not None:
            pairs.add((routine_id, r.scope_slug))
    for routine_id, default_scope_slug in default_scope_by_routine.items():
        pairs.add((routine_id, default_scope_slug))

    if pairs:
        bind.execute(
            insert(_routine_scopes),
            [{"routine_id": routine_id, "scope_slug": scope_slug} for routine_id, scope_slug in sorted(pairs)],
        )


def downgrade() -> None:
    bind = op.get_bind()
    _routine_scopes.drop(bind, checkfirst=True)
