"""routine slug — a routine's lineage and handle, beside a free display name.

Adds `routines.slug` (server default `''`), fills it from `name` in bounded pages, and tightens it to NOT NULL keeping
the default. Replaces `uq_routines_project_name` with `uq_routines_project_slug (project_id, slug)` and rebuilds
`ix_routines_tenant_name` on the slug as `ix_routines_tenant_slug`. Rewrites no lineage row and writes no
`config_changes` row. Downgrade first sets `name := slug`, so a walk back loses only edited display names and the name
constraint can always be restored.
Revision ID: 20261105_0900_routine_slug
Revises: 20261104_0900_drop_graph_nodes_run
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0900_routine_slug"
down_revision: str | None = "20261104_0900_drop_graph_nodes_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TABLE = "routines"
_SLUG = "slug"
_DEFAULT = ""
_UNIQUE_NAME = "uq_routines_project_name"
_UNIQUE_SLUG = "uq_routines_project_slug"
_INDEX_NAME = "ix_routines_tenant_name"
_INDEX_SLUG = "ix_routines_tenant_slug"

#: The reshaped table, frozen to the columns this revision reads and fills.
_frozen = sa.MetaData()
_routines = sa.Table(
    _TABLE,
    _frozen,
    sa.Column("routine_id", sa.String(), primary_key=True),
    sa.Column("name", sa.String()),
    sa.Column(_SLUG, sa.String()),
)


def _columns(bind: sa.Connection) -> dict[str, bool]:
    """Each column of `routines` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(_TABLE)}


def _index_names(bind: sa.Connection) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(_TABLE)}


def _unique_names(bind: sa.Connection) -> set[str]:
    return {str(u["name"]) for u in sa.inspect(bind).get_unique_constraints(_TABLE)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_TABLE}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps what its own reflection would drop."""
    return sa.Table(_TABLE, sa.MetaData(), autoload_with=bind)


def _add_column(bind: sa.Connection) -> None:
    """Step 1: the slug, nullable, added in place carrying its server default from the start."""
    if _SLUG not in _columns(bind):
        op.add_column(_TABLE, sa.Column(_SLUG, sa.String(), nullable=True, server_default=_DEFAULT))


def _fill(bind: sa.Connection) -> None:
    """Step 2: every row still holding no slug takes its name, in bounded pages along the table's key."""
    unfilled = sa.or_(_routines.c[_SLUG].is_(None), _routines.c[_SLUG] == _DEFAULT)
    page = sa.select(_routines.c.routine_id).where(unfilled).order_by(_routines.c.routine_id).limit(_BATCH_ROWS)
    while keys := list(bind.execute(page).scalars()):
        bind.execute(
            sa.update(_routines).where(_routines.c.routine_id.in_(keys)).values({_SLUG: _routines.c.name})
        )


def _tighten(bind: sa.Connection) -> None:
    """Step 3: the slug becomes required keeping its default, and the name's unique constraint gives way to the
    slug's, in one batch."""
    nullable = _columns(bind)[_SLUG]
    swap = _UNIQUE_NAME in _unique_names(bind) or _UNIQUE_SLUG not in _unique_names(bind)
    if not nullable and not swap:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_TABLE, recreate="auto", copy_from=_reflected(bind)) as batch:
        if nullable:
            batch.alter_column(
                _SLUG,
                existing_type=sa.String(),
                existing_server_default=sa.text(f"'{_DEFAULT}'"),
                server_default=_DEFAULT,
                nullable=False,
            )
        if _UNIQUE_NAME in _unique_names(bind):
            batch.drop_constraint(_UNIQUE_NAME, type_="unique")
        if _UNIQUE_SLUG not in _unique_names(bind):
            batch.create_unique_constraint(_UNIQUE_SLUG, ["project_id", _SLUG])


def _rebuild_index(bind: sa.Connection) -> None:
    """Step 4: the tenant-wide handle index moves from the name to the slug."""
    if _INDEX_NAME in _index_names(bind):
        op.drop_index(_INDEX_NAME, table_name=_TABLE)
    if _INDEX_SLUG not in _index_names(bind):
        op.create_index(_INDEX_SLUG, _TABLE, ["tenant_id", _SLUG])


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_add_column, _fill, _tighten, _rebuild_index)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _SLUG not in _columns(bind):
        return
    held = sa.and_(_routines.c[_SLUG].is_not(None), _routines.c[_SLUG] != _DEFAULT)
    bind.execute(sa.update(_routines).where(held).values(name=_routines.c[_SLUG]))
    if _INDEX_SLUG in _index_names(bind):
        op.drop_index(_INDEX_SLUG, table_name=_TABLE)
    _clear_stale_copy(bind)
    with op.batch_alter_table(_TABLE, recreate="auto", copy_from=_reflected(bind)) as batch:
        if _UNIQUE_SLUG in _unique_names(bind):
            batch.drop_constraint(_UNIQUE_SLUG, type_="unique")
        batch.drop_column(_SLUG)
        if _UNIQUE_NAME not in _unique_names(bind):
            batch.create_unique_constraint(_UNIQUE_NAME, ["project_id", "name"])
    if _INDEX_NAME not in _index_names(bind):
        op.create_index(_INDEX_NAME, _TABLE, ["tenant_id", "name"])
