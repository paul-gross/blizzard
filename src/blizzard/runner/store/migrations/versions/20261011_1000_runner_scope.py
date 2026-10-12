"""runner scope — every runner-scoped table gains a nullable ``runner_id``, filled from the store's identity row.

Each table in :data:`_KEYED` gains ``runner_id``, nullable and appended last, in a batch of its own under
``recreate="auto"``; ``env_bindings`` gains its nullable ``workspace_id`` and ``project_id`` in the same batch. Every
row is then stamped with the id the store's ``runner_identity`` row names, in one pass along the primary key in bounded
batches. A store holding no identity leaves every ``runner_id`` NULL, for the runner to stamp when it binds.
``runner_identity`` keeps its own ``runner_id`` and gains nothing. The three host-scoped tables ``host_identity``,
``host_outbound_buffer`` and ``host_high_water_seen`` are created empty.

No existing constraint, index or column changes, so the previous release keeps writing through its own statements; its
rows simply carry no runner. ``NOT NULL`` and the indexes led by ``runner_id`` come in a later revision.

Re-running the upgrade skips a table whose columns are already there, creates only the host tables still missing, and
stamps only rows still null. The downgrade drops exactly what the upgrade added.
Revision ID: 20261011_1000_runner_scope
Revises: 20261009_1000_runner_lease_closures_lease_id_index
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261011_1000_runner_scope"
down_revision: str | None = "20261009_1000_runner_lease_closures_lease_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNER_COLUMN = "runner_id"
_BATCH_ROWS = 1000

# Every runner-scoped table but ``runner_identity`` as the store held it at this revision, with the primary-key columns
# the backfill pages by — frozen here, never derived from ``schema.metadata`` (``bzh:keyed-table-migration``).
_KEYED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("asks", ("id",)),
    ("attachments", ("id",)),
    ("binding_releases", ("id",)),
    ("check_results", ("id",)),
    ("checks_ran", ("id",)),
    ("context_samples", ("id",)),
    ("env_bindings", ("id",)),
    ("escalation_closures", ("id",)),
    ("git_commit_declarations", ("id",)),
    ("heartbeats", ("id",)),
    ("hub_control", ("id",)),
    ("hub_served_projects", ("id",)),
    ("in_flight_elicitations", ("id",)),
    ("invocation_boundaries", ("id",)),
    ("invocation_boundary_advances", ("id",)),
    ("lease_closures", ("id",)),
    ("lease_context", ("lease_id",)),
    ("lease_spawns", ("id",)),
    ("lease_tokens", ("lease_id",)),
    ("leases", ("lease_id",)),
    ("local_pause_facts", ("id",)),
    ("nudge_facts", ("id",)),
    ("outbound_buffer", ("seq",)),
    ("overload_facts", ("id",)),
    ("overload_resets", ("id",)),
    ("park_facts", ("id",)),
    ("park_resumes", ("id",)),
    ("pause_park_resumes", ("id",)),
    ("pause_parks", ("id",)),
    ("requeues", ("id",)),
    ("resume_clears", ("id",)),
    ("resume_intents", ("id",)),
    ("route_tokens", ("chunk_id",)),
    ("selftest_results", ("id",)),
    ("session_ends", ("id",)),
    ("session_preamble_facts", ("id",)),
    ("takeover_ends", ("id",)),
    ("takeovers", ("takeover_id",)),
    ("trace_cursor", ("id",)),
    ("trace_export_latch", ("id",)),
    ("transcript_outbound_buffer", ("seq",)),
    ("transcript_segments", ("segment_id",)),
    ("usage_facts", ("id",)),
)

# The columns each table gains ahead of ``runner_id``, in the same batch.
# The keyed tables the parent revision created with ``sqlite_autoincrement``.
_AUTOINCREMENT = frozenset({"outbound_buffer", "transcript_outbound_buffer", "in_flight_elicitations"})

_EXTRA_COLUMNS: dict[str, tuple[str, ...]] = {"env_bindings": ("workspace_id", "project_id")}

# The host-scoped tables this revision creates, frozen at their created shape (``bzh:frozen-revisions``).
_frozen = sa.MetaData()
_HOST_TABLES = (
    sa.Table(
        "host_identity",
        _frozen,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("host_id", sa.String, nullable=False),
        sa.Column("tenant_id", sa.String, nullable=True),
        sa.Column("registered_at", UtcDateTime, nullable=False),
    ),
    sa.Table(
        "host_outbound_buffer",
        _frozen,
        sa.Column("seq", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("created_at", UtcDateTime, nullable=False),
        sa.Column("acked_at", UtcDateTime, nullable=True),
        sqlite_autoincrement=True,
    ),
    sa.Table(
        "host_high_water_seen",
        _frozen,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("updated_at", UtcDateTime, nullable=False),
    ),
)

_runner_identity = sa.table("runner_identity", sa.column("id", sa.Integer), sa.column(_RUNNER_COLUMN, sa.String))


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(column["name"]) for column in sa.inspect(bind).get_columns(table)}


def _stored_runner_id(bind: sa.Connection) -> str | None:
    """The id the store's identity row names — the newest row, should a store hold more than one — or None."""
    statement = sa.select(_runner_identity.c[_RUNNER_COLUMN]).order_by(_runner_identity.c.id.desc()).limit(1)
    return bind.execute(statement).scalar_one_or_none()


def _add_columns(bind: sa.Connection, table: str) -> None:
    present = _columns(bind, table)
    missing = [name for name in (*_EXTRA_COLUMNS.get(table, ()), _RUNNER_COLUMN) if name not in present]
    if not missing:
        return
    with op.batch_alter_table(table, recreate="auto") as batch:
        for name in missing:
            batch.add_column(sa.Column(name, sa.String(), nullable=True))


def _backfill(bind: sa.Connection, table: str, primary_key: tuple[str, ...], runner_id: str) -> None:
    """Stamp the rows still null in one pass along the primary key, at most ``_BATCH_ROWS`` per statement.

    Each page seeks past the last key the page before it stamped instead of scanning from the start, so the work grows
    with the row count, not its square.
    """
    stub = sa.table(table, *(sa.column(name) for name in primary_key), sa.column(_RUNNER_COLUMN, sa.String))
    columns = [stub.c[name] for name in primary_key]
    key = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    unstamped = stub.c[_RUNNER_COLUMN].is_(None)
    page = sa.select(*columns).where(unstamped).order_by(*columns).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        last = tuple(rows[-1]) if len(columns) > 1 else rows[-1][0]
        bind.execute(sa.update(stub).where(unstamped, after, key <= last).values({_RUNNER_COLUMN: runner_id}))
        after = key > last


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for host_table in _HOST_TABLES:
        if host_table.name not in existing:
            host_table.create(bind)
    runner_id = _stored_runner_id(bind)
    for table, primary_key in _KEYED:
        _add_columns(bind, table)
        if runner_id is not None:
            _backfill(bind, table, primary_key, runner_id)


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in reversed(_KEYED):
        present = _columns(bind, table)
        added = [name for name in (_RUNNER_COLUMN, *reversed(_EXTRA_COLUMNS.get(table, ()))) if name in present]
        if not added:
            continue
        # The recreate copies the table through a fresh CREATE, so a table the parent declared AUTOINCREMENT names it
        # again, or pruned ids would be reused.
        kwargs = {"sqlite_autoincrement": True} if table in _AUTOINCREMENT else {}
        with op.batch_alter_table(table, recreate="auto", table_kwargs=kwargs) as batch:
            for name in added:
                batch.drop_column(name)
    existing = set(sa.inspect(bind).get_table_names())
    for host_table in reversed(_HOST_TABLES):
        if host_table.name in existing:
            host_table.drop(bind)
