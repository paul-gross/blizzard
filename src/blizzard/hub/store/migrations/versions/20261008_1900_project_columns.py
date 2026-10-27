"""project columns — a runner records the projects it serves, and a landed repository row the repository record it
landed into.

``runner_registrations`` gains ``projects``, a nullable JSON list of ``{entry, project_id}`` pairs: what the runner
declared and the project each entry resolved to; a null reads as serving nothing. ``delivery_repo_landed`` gains
``repository_name``, the name of the repository record a landing resolved to; it is nullable, and every row landed
before this revision keeps a null — the record a past landing resolved to was never kept, and deriving it from the
row's ``owner/repo`` would be a guess. Both columns are added in place.

Every runner row whose ``projects`` is null is given its tenant's one project, as an entry naming the project's id,
so every runner keeps claiming exactly what it claimed before. The rows are filled per tenant, paging along
``runner_id`` in bounded batches, each page seeking past the last id the one before it covered. The upgrade refuses,
naming the tenants, while a tenant holding such a runner holds other than one project. Each step inspects first and
skips what a stopped run already did: a column already added is not added again, and a row already declaring its
projects is not rewritten.

The downgrade drops both columns; what every runner declared it serves and the repository record of every landing
are lost.
Revision ID: 20261008_1900_project_columns
Revises: 20261008_1810_project_read_indexes
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1900_project_columns"
down_revision: str | None = "20261008_1810_project_read_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_RUNNERS = "runner_registrations"
_SERVED = "projects"
_LANDED = "delivery_repo_landed"
_REPOSITORY_NAME = "repository_name"
#: Each added column: its table, its name and its type.
_ADDED: tuple[tuple[str, str, sa.types.TypeEngine[str]], ...] = (
    (_RUNNERS, _SERVED, sa.Text()),
    (_LANDED, _REPOSITORY_NAME, sa.String()),
)

_projects = sa.table("projects", sa.column("project_id", sa.String), sa.column("tenant_id", sa.String))
_runners = sa.table(
    _RUNNERS, sa.column("runner_id", sa.String), sa.column("tenant_id", sa.String), sa.column(_SERVED, sa.Text)
)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _add_columns(bind: sa.Connection) -> None:
    """Step 1: both columns, nullable, added in place."""
    for table, column, kind in _ADDED:
        if column not in _columns(bind, table):
            op.add_column(table, sa.Column(column, kind, nullable=True))


def _undeclared(tenant: str | None = None) -> sa.ColumnElement[bool]:
    unset = _runners.c[_SERVED].is_(None)
    return unset if tenant is None else sa.and_(unset, _runners.c.tenant_id == tenant)


def _refuse_ambiguous_tenants(bind: sa.Connection, tenants: list[str]) -> dict[str, str]:
    """Each named tenant's one project; refuse, naming them, while any of them holds other than one."""
    held: dict[str, list[str]] = {tenant: [] for tenant in tenants}
    rows = bind.execute(
        sa.select(_projects.c.tenant_id, _projects.c.project_id)
        .where(_projects.c.tenant_id.in_(tenants))
        .order_by(_projects.c.tenant_id, _projects.c.project_id)
    )
    for tenant, project in rows:
        held[tenant].append(project)
    ambiguous = [tenant for tenant, projects in held.items() if len(projects) != 1]
    if ambiguous:
        raise RuntimeError(
            f"{', '.join(ambiguous)} each hold a runner but not exactly one project; the projects such a runner serves"
            " cannot be chosen for it — leave each of those tenants one project before upgrading"
        )
    return {tenant: projects[0] for tenant, projects in held.items()}


def _declare(bind: sa.Connection) -> None:
    """Step 2: every runner row declaring no projects serves its tenant's one project."""
    tenant_column = _runners.c.tenant_id
    tenants = list(
        bind.execute(sa.select(tenant_column).where(_undeclared()).distinct().order_by(tenant_column)).scalars()
    )
    if not tenants:
        return
    for tenant, project in _refuse_ambiguous_tenants(bind, tenants).items():
        served = json.dumps([{"entry": project, "project_id": project}])
        unset = _undeclared(tenant)
        page = sa.select(_runners.c.runner_id).where(unset).order_by(_runners.c.runner_id).limit(_BATCH_ROWS)
        after: sa.ColumnElement[bool] = sa.true()
        while ids := bind.execute(page.where(after)).scalars().all():
            last = ids[-1]
            bind.execute(
                sa.update(_runners).where(unset, after, _runners.c.runner_id <= last).values({_SERVED: served})
            )
            after = _runners.c.runner_id > last


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_add_columns, _declare)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, _kind in reversed(_ADDED):
        if column not in _columns(bind, table):
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            batch.drop_column(column)
