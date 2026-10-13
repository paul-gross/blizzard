"""hosts — a tenant-scoped record above runners, with a bearer credential of its own.

Creates ``hosts``, ``host_lifecycle_facts`` and ``host_token_revocations``, each tenant-keyed, and adds a nullable
``runner_registrations.host_id`` with its key and a tenant-led index; existing runners stand alone. Each step inspects
first and skips what a stopped run already did. The downgrade refuses, naming them, while any runner stands under a host.
Revision ID: 20261012_1000_hosts
Revises: 20261011_1300_chunk_recorded_at_indexes
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261012_1000_hosts"
down_revision: str | None = "20261011_1300_chunk_recorded_at_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
_HOSTS = "hosts"
_LIFECYCLE = "host_lifecycle_facts"
_REVOCATIONS = "host_token_revocations"
_RUNNERS = "runner_registrations"
_HOST_COLUMN = "host_id"
_RUNNER_HOST_FK = "fk_runner_registrations_hosts"
_RUNNER_HOST_INDEX = "ix_runner_registrations_tenant_host_id"
#: In the order they are created; dropped in reverse.
_NEW_TABLES = (_HOSTS, _LIFECYCLE, _REVOCATIONS)


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _foreign_key_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(f["name"]) for f in sa.inspect(bind).get_foreign_keys(table)}


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _host_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_HOST_COLUMN], ["hosts.host_id"], name=f"fk_{table}_hosts")


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy a batch left behind when its run stopped part way.

    SQLite creates the copy outside the transaction the rest of the batch runs in, so a stopped run can keep it while
    the table itself is rolled back; the name is the one Alembic's batch gives it.
    """
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, the structure every copying batch starts from: a batch left to reflect for
    itself drops an unnamed ``CHECK`` constraint from the copy."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def _create_tables(bind: sa.Connection) -> None:
    """Step 1: the three tables, each skipped when it exists."""
    if not _has_table(bind, _HOSTS):
        op.create_table(
            _HOSTS,
            sa.Column(_HOST_COLUMN, sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("added_at", UtcDateTime(), nullable=False),
            sa.Column("added_by", sa.String(), nullable=True),
            sa.Column("registered_at", UtcDateTime(), nullable=True),
            sa.Column("last_seen_at", UtcDateTime(), nullable=True),
            sa.Column("url", sa.Text(), nullable=True),
            sa.Column("redirect_uris", sa.Text(), nullable=True),
            sa.Column("workspaces", sa.Text(), nullable=True),
            sa.Column("subscriptions", sa.Text(), nullable=True),
            sa.Column("token_hash", sa.Text(), nullable=True),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(_HOST_COLUMN, name="pk_hosts"),
            _tenant_fk(_HOSTS),
        )
    for name, columns in (
        ("ix_hosts_token_hash", ["token_hash"]),
        ("ix_hosts_tenant_id", [_TENANT_COLUMN]),
    ):
        if name not in _index_names(bind, _HOSTS):
            op.create_index(name, _HOSTS, columns)
    if not _has_table(bind, _LIFECYCLE):
        op.create_table(
            _LIFECYCLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_HOST_COLUMN, sa.String(), nullable=False),
            sa.Column("retired", sa.Boolean(), nullable=False),
            sa.Column("set_at", UtcDateTime(), nullable=False),
            sa.Column("set_by", sa.String(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            _host_fk(_LIFECYCLE),
            _tenant_fk(_LIFECYCLE),
        )
    if f"ix_{_LIFECYCLE}_tenant_id" not in _index_names(bind, _LIFECYCLE):
        op.create_index(f"ix_{_LIFECYCLE}_tenant_id", _LIFECYCLE, [_TENANT_COLUMN])
    if not _has_table(bind, _REVOCATIONS):
        op.create_table(
            _REVOCATIONS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(_HOST_COLUMN, sa.String(), nullable=False),
            sa.Column("token_hash", sa.Text(), nullable=False),
            sa.Column("revoked_at", UtcDateTime(), nullable=False),
            sa.Column("revoked_by", sa.String(), nullable=False),
            sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
            _host_fk(_REVOCATIONS),
            _tenant_fk(_REVOCATIONS),
        )
    for name, columns in (
        (f"ix_{_REVOCATIONS}_token_hash", ["token_hash"]),
        (f"ix_{_REVOCATIONS}_tenant_id", [_TENANT_COLUMN]),
    ):
        if name not in _index_names(bind, _REVOCATIONS):
            op.create_index(name, _REVOCATIONS, columns)


def _add_runner_host(bind: sa.Connection) -> None:
    """Step 2: ``runner_registrations.host_id``, nullable, with its foreign key to ``hosts``."""
    has_column = _HOST_COLUMN in _columns(bind, _RUNNERS)
    has_key = _RUNNER_HOST_FK in _foreign_key_names(bind, _RUNNERS)
    if has_column and has_key:
        return
    _clear_stale_copy(bind, _RUNNERS)
    with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind, _RUNNERS)) as batch:
        if not has_column:
            batch.add_column(sa.Column(_HOST_COLUMN, sa.String(), nullable=True))
        if not has_key:
            batch.create_foreign_key(_RUNNER_HOST_FK, _HOSTS, [_HOST_COLUMN], [_HOST_COLUMN])


def _index_runner_host(bind: sa.Connection) -> None:
    """Step 3: the tenant-led index the reads of a host's runners use."""
    if _RUNNER_HOST_INDEX not in _index_names(bind, _RUNNERS):
        op.create_index(_RUNNER_HOST_INDEX, _RUNNERS, [_TENANT_COLUMN, _HOST_COLUMN])


#: The upgrade's steps, in order; each inspects first and skips what a stopped run already did.
UPGRADE_STEPS: tuple[Callable[[sa.Connection], None], ...] = (_create_tables, _add_runner_host, _index_runner_host)


def upgrade() -> None:
    bind = op.get_bind()
    for step in UPGRADE_STEPS:
        step(bind)


def _refuse_hosted_runners(bind: sa.Connection) -> None:
    if _HOST_COLUMN not in _columns(bind, _RUNNERS):
        return
    runners = sa.table(_RUNNERS, sa.column("runner_id", sa.String), sa.column(_HOST_COLUMN, sa.String))
    hosted = (
        bind.execute(sa.select(runners.c.runner_id).where(runners.c[_HOST_COLUMN].is_not(None)).order_by(runners.c.runner_id))
        .scalars()
        .all()
    )
    if hosted:
        raise RuntimeError(
            f"cannot downgrade past {revision}: {len(hosted)} runner(s) stand under a host: " + ", ".join(hosted)
        )


def downgrade() -> None:
    bind = op.get_bind()
    _refuse_hosted_runners(bind)
    if _RUNNER_HOST_INDEX in _index_names(bind, _RUNNERS):
        op.drop_index(_RUNNER_HOST_INDEX, table_name=_RUNNERS)
    if _HOST_COLUMN in _columns(bind, _RUNNERS):
        _clear_stale_copy(bind, _RUNNERS)
        with op.batch_alter_table(_RUNNERS, recreate="auto", copy_from=_reflected(bind, _RUNNERS)) as batch:
            if _RUNNER_HOST_FK in _foreign_key_names(bind, _RUNNERS):
                batch.drop_constraint(_RUNNER_HOST_FK, type_="foreignkey")
            batch.drop_column(_HOST_COLUMN)
    for table in reversed(_NEW_TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
