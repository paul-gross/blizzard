"""questions and decisions carry their chunk's project.

``questions`` and ``decisions`` gain a ``project_id``, added nullable, filled from the row's chunk — paging along each
table's key in bounded batches — then made required with ``fk_questions_projects`` and ``fk_decisions_projects``. A
project-led index on each stands beside the tenant-led ones, which keep serving the reads that name no project. The
upgrade refuses to finish, naming the tenants, if any row is left without a project. Each step inspects first and skips
what a stopped run already did.

The downgrade drops the indexes, the foreign keys and the columns.
Revision ID: 20261014_0900_question_decision_project
Revises: 20261013_0900_host_fact_lane
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0900_question_decision_project"
down_revision: str | None = "20261013_0900_host_fact_lane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_TENANT_COLUMN = "tenant_id"
_PROJECT_COLUMN = "project_id"
_PROJECTS = "projects"

#: Each table the revision touches and the key its fill pages along — `questions` ids are runner-minted, so unique only
#: within the tenant.
_OWNED: dict[str, tuple[str, ...]] = {
    "questions": (_TENANT_COLUMN, "question_id"),
    "decisions": ("decision_id",),
}

#: Indexes this revision adds: name, table, columns.
_ADDED_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "ix_questions_tenant_project_asked_at_question_id",
        "questions",
        ("tenant_id", "project_id", "asked_at", "question_id"),
    ),
    (
        "ix_decisions_tenant_project_submitted_at_decision_id",
        "decisions",
        ("tenant_id", "project_id", "submitted_at", "decision_id"),
    ),
)

_chunks = sa.table(
    "chunks",
    sa.column("chunk_id", sa.String),
    sa.column(_TENANT_COLUMN, sa.String),
    sa.column(_PROJECT_COLUMN, sa.String),
)


def _columns(bind: sa.Connection, table: str) -> dict[str, bool]:
    """Each column of ``table`` mapped to whether it is nullable."""
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(bind).get_columns(table)}


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


def _foreign_key_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(fk["name"]) for fk in sa.inspect(bind).get_foreign_keys(table) if fk.get("name")}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _stub(table: str, key: tuple[str, ...]) -> sa.TableClause:
    names = dict.fromkeys((*key, "chunk_id", _TENANT_COLUMN, _PROJECT_COLUMN))
    return sa.table(table, *(sa.column(name, sa.String) for name in names))


def _add_columns(bind: sa.Connection) -> None:
    """Step 1: a nullable ``project_id`` on each table, added in place."""
    for table in _OWNED:
        if _PROJECT_COLUMN not in _columns(bind, table):
            op.add_column(table, sa.Column(_PROJECT_COLUMN, sa.String(), nullable=True))


def _fill(bind: sa.Connection, table: str, key: tuple[str, ...]) -> None:
    """Set ``project_id`` from the row's chunk on the rows where it is null, in one pass along ``key``, at most
    ``_BATCH_ROWS`` per statement, each page seeking past the last key the one before it covered."""
    t = _stub(table, key)
    project = (
        sa.select(_chunks.c.project_id)
        .where(_chunks.c.chunk_id == t.c.chunk_id, _chunks.c.tenant_id == t.c.tenant_id)
        .scalar_subquery()
    )
    unset = t.c[_PROJECT_COLUMN].is_(None)
    columns = [t.c[name] for name in key]
    seek = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    page = sa.select(*columns).where(unset).order_by(*columns).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        last = tuple(rows[-1]) if len(columns) > 1 else rows[-1][0]
        bind.execute(sa.update(t).where(unset, after, seek <= last).values({_PROJECT_COLUMN: project}))
        after = seek > last


def _stamp(bind: sa.Connection) -> None:
    """Step 2: every question and decision names its chunk's project."""
    for table, key in _OWNED.items():
        _fill(bind, table, key)


def _tighten(bind: sa.Connection) -> None:
    """Step 3: one batch per table — ``project_id`` required, with its foreign key."""
    for table in _OWNED:
        require = _columns(bind, table)[_PROJECT_COLUMN]
        attach = f"fk_{table}_projects" not in _foreign_key_names(bind, table)
        if not (require or attach):
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            if require:
                batch.alter_column(_PROJECT_COLUMN, existing_type=sa.String(), nullable=False)
            if attach:
                batch.create_foreign_key(f"fk_{table}_projects", _PROJECTS, [_PROJECT_COLUMN], [_PROJECT_COLUMN])


def _add_indexes(bind: sa.Connection) -> None:
    """Step 4: the project-led indexes, added beside the tenant-led ones."""
    for name, table, columns in _ADDED_INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def _verify(bind: sa.Connection) -> None:
    """Step 5: refuse to finish while a row names no project."""
    for table, key in _OWNED.items():
        t = _stub(table, key)
        unset = bind.execute(
            sa.select(t.c.tenant_id).where(t.c.project_id.is_(None)).distinct().order_by(t.c.tenant_id)
        )
        named = list(unset.scalars())
        if named:
            raise RuntimeError(f"{table} rows of {', '.join(named)} name no project")


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_add_columns, _stamp, _tighten, _add_indexes, _verify)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _ in _ADDED_INDEXES:
        if name in _index_names(bind, table):
            op.drop_index(name, table_name=table)
    for table in reversed(_OWNED):
        if _PROJECT_COLUMN not in _columns(bind, table):
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            if f"fk_{table}_projects" in _foreign_key_names(bind, table):
                batch.drop_constraint(f"fk_{table}_projects", type_="foreignkey")
            batch.drop_column(_PROJECT_COLUMN)
