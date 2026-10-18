"""project_color — every project holds a color, one name from the palette.

Added nullable, filled per tenant in bounded pages, then required. The downgrade drops the column and its colors.
Revision ID: 20261018_1200_project_color
Revises: 20261018_0900_decision_check_results
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261018_1200_project_color"
down_revision: str | None = "20261018_0900_decision_check_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_PROJECTS = "projects"
_COLOR = "color"
#: The palette as this revision assigns it, in order — frozen here, never read from the live vocabulary.
_PALETTE: tuple[str, ...] = (
    "red",
    "orange",
    "amber",
    "lime",
    "green",
    "teal",
    "cyan",
    "blue",
    "indigo",
    "violet",
    "pink",
    "slate",
)

_projects = sa.table(
    _PROJECTS, sa.column("project_id", sa.String), sa.column("tenant_id", sa.String), sa.column(_COLOR, sa.String)
)


def _columns(bind: sa.Connection) -> dict[str, bool]:
    """Each column of ``projects`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(_PROJECTS)}


def _add_column(bind: sa.Connection) -> None:
    """Step 1: the column, nullable, added in place."""
    if _COLOR not in _columns(bind):
        op.add_column(_PROJECTS, sa.Column(_COLOR, sa.String(), nullable=True))


def _fill(bind: sa.Connection) -> None:
    """Step 2: every project without a color takes the palette entry at its rank among its tenant's projects."""
    tenant_column = _projects.c.tenant_id
    tenants = list(
        bind.execute(
            sa.select(tenant_column).where(_projects.c[_COLOR].is_(None)).distinct().order_by(tenant_column)
        ).scalars()
    )
    for tenant in tenants:
        page = (
            sa.select(_projects.c.project_id, _projects.c[_COLOR])
            .where(tenant_column == tenant)
            .order_by(_projects.c.project_id)
            .limit(_BATCH_ROWS)
        )
        after: sa.ColumnElement[bool] = sa.true()
        rank = 0
        while rows := bind.execute(page.where(after)).all():
            unset = [
                {"pid": project_id, "fill": _PALETTE[(rank + offset) % len(_PALETTE)]}
                for offset, (project_id, color) in enumerate(rows)
                if color is None
            ]
            if unset:
                bind.execute(
                    sa.update(_projects)
                    .where(_projects.c.project_id == sa.bindparam("pid"))
                    .values({_COLOR: sa.bindparam("fill")}),
                    unset,
                )
            rank += len(rows)
            after = _projects.c.project_id > rows[-1][0]


def _tighten(bind: sa.Connection) -> None:
    """Step 3: the column becomes required, in one batch over the table as the store holds it."""
    if not _columns(bind)[_COLOR]:
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_PROJECTS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.alter_column(_COLOR, existing_type=sa.String(), nullable=False)


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_add_column, _fill, _tighten)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a batch left behind when its run stopped part way.

    SQLite creates the copy outside the transaction the rest of the batch runs in, so a stopped run can keep it while
    the table itself is rolled back; the name is the one Alembic's batch gives it.
    """
    copy = f"_alembic_tmp_{_PROJECTS}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, the structure every copying batch starts from: a batch left to reflect for
    itself drops an unnamed ``CHECK`` constraint from the copy."""
    return sa.Table(_PROJECTS, sa.MetaData(), autoload_with=bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _COLOR not in _columns(bind):
        return
    _clear_stale_copy(bind)
    with op.batch_alter_table(_PROJECTS, recreate="auto", copy_from=_reflected(bind)) as batch:
        batch.drop_column(_COLOR)
