"""runner scope tighten — every runner-scoped row names its runner, and the outboxes count per runner.

The upgrade refuses, before touching anything, a store that holds runner-scoped rows but no ``runner_identity`` row: the
rows belong to a runner the store cannot name. Otherwise it stamps any row still lacking a runner from the identity row,
in bounded batches along each primary key, and then brings each table of :data:`_KEYED`, one batch per table under
``recreate="auto"``, to its tight shape:

* ``runner_id`` becomes ``NOT NULL`` on every table;
* ``route_tokens`` is keyed ``(runner_id, chunk_id)``, and the two outboxes ``(runner_id, seq)``;
* ``runner_identity`` and ``hub_control`` hold one row per runner, and ``hub_served_projects`` is unique on
  ``(runner_id, position)``.

``in_flight_elicitations`` keeps its AUTOINCREMENT, and on SQLite its ``sqlite_sequence`` counter is read before each
rebuild and written back after it at no less than it was, in both directions.

Before the outboxes lose their autoincrement, each lane's counter is seeded in ``outbox_counters`` (one row per runner
and lane, created here): a probe row is inserted through the table's own autoincrement, the seq it was issued is read
back, and the probe is deleted. The counter starts at the seq before it — the greatest the store has issued, whether or
not a row still holds it — so no new fact can land at or below a seq a consumer already treated as final.

The upgrade is one transaction from its first data write, so a stopped run leaves the parent's shape and only DDL ahead
of that write can survive: the empty ``outbox_counters`` table in any store, and, in a store with no identity row, the
first batch's table copy. Re-running it skips a table already at its tight shape, seeds no counter once its outbox is
rebuilt (which holds because the seeding and the rebuild commit together), and stamps only rows still null.

The downgrade refuses while the store holds more than one runner, naming them. With one runner, each lane keeps an acked
row at its counter's last-issued seq, re-inserted if retention pruned it, so the autoincrement a rebuilt outbox restores
resumes above it on SQLite (Postgres keeps each outbox's own sequence, which the counters never advanced, so there the
probe refuses once a runner has written past it); a probe through the restored autoincrement that lands at or below the counter refuses the downgrade,
naming the pre-migrate backup; on SQLite the probe leaves each outbox's counter where it found it, so the next seq is
the one after the last issued. It then drops ``outbox_counters`` and restores the keys and nullability the parent
revision had.
Revision ID: 20261013_1000_runner_scope_tighten
Revises: 20261011_1000_runner_scope
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import sqlalchemy as sa
from alembic import op
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.migrations import MigrationRefused
from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261013_1000_runner_scope_tighten"
down_revision: str | None = "20261011_1000_runner_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNER_COLUMN = "runner_id"
_BATCH_ROWS = 1000

# Every runner-scoped table but ``runner_identity`` as the store held it before this revision, with the primary-key
# columns the backfill pages by — frozen here, never derived from ``schema.metadata`` (``bzh:keyed-table-migration``).
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

# The tables that keep their SQLite AUTOINCREMENT through a rebuild: a recreate copies the table through a fresh CREATE,
# so one the parent declared AUTOINCREMENT names it again, and ``_keep_sequence`` carries its ``sqlite_sequence`` counter
# over, or pruned ids would be reused. Postgres needs neither: its identity survives ``ALTER COLUMN``.
_AUTOINCREMENT = frozenset({"in_flight_elicitations"})
_OUTBOXES = frozenset({"outbound_buffer", "transcript_outbound_buffer"})

# The tables whose primary key leads with the runner: (constraint name, columns) as tightened, and the parent's
# primary-key columns with the name a store gives it unnamed.
_TIGHT_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "outbound_buffer": ("pk_outbound_buffer", (_RUNNER_COLUMN, "seq")),
    "transcript_outbound_buffer": ("pk_transcript_outbound_buffer", (_RUNNER_COLUMN, "seq")),
    "route_tokens": ("pk_route_tokens", (_RUNNER_COLUMN, "chunk_id")),
}
_LOOSE_KEYS: dict[str, tuple[str, tuple[str, ...]]] = {
    "outbound_buffer": ("outbound_buffer_pkey", ("seq",)),
    "transcript_outbound_buffer": ("transcript_outbound_buffer_pkey", ("seq",)),
    "route_tokens": ("route_tokens_pkey", ("chunk_id",)),
}

# The unique constraints, by table: (name, columns) as tightened, and as the parent held them (``None`` for none).
_TIGHT_UNIQUES: dict[str, tuple[str, tuple[str, ...]]] = {
    "hub_control": ("uq_hub_control_runner_id", (_RUNNER_COLUMN,)),
    "runner_identity": ("uq_runner_identity_runner_id", (_RUNNER_COLUMN,)),
    "hub_served_projects": ("uq_hub_served_projects_runner_id_position", (_RUNNER_COLUMN, "position")),
}
_LOOSE_UNIQUES: dict[str, tuple[str, tuple[str, ...]] | None] = {
    "hub_control": None,
    "runner_identity": None,
    "hub_served_projects": ("uq_hub_served_projects_position", ("position",)),
}

# The tables this revision reshapes beyond ``runner_id``'s nullability: ``runner_identity`` joins them for its unique.
_TABLES: tuple[str, ...] = (*(name for name, _ in _KEYED), "runner_identity")

_COUNTERS = "outbox_counters"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# The outbox lanes: (lane, table, the columns a probe row must name beside ``runner_id``).
_LANES: tuple[tuple[str, str, dict[str, object]], ...] = (
    ("outbound", "outbound_buffer", {"kind": "probe", "payload": "{}", "created_at": _EPOCH}),
    (
        "transcript",
        "transcript_outbound_buffer",
        {"segment_id": "probe", "chunk_id": "probe", "final": False, "payload": "{}", "created_at": _EPOCH},
    ),
)

# The counter table as this revision creates it, frozen (``bzh:frozen-revisions``).
_frozen = sa.MetaData()
_outbox_counters = sa.Table(
    _COUNTERS,
    _frozen,
    sa.Column("runner_id", sa.String, nullable=False),
    sa.Column("lane", sa.String, nullable=False),
    sa.Column("last_seq", sa.Integer, nullable=False),
    sa.PrimaryKeyConstraint("runner_id", "lane", name="pk_outbox_counters"),
)

_runner_identity = sa.table("runner_identity", sa.column("id", sa.Integer), sa.column(_RUNNER_COLUMN, sa.String))


def _stored_runner_id(bind: sa.Connection) -> str | None:
    """The id the store's identity row names — the newest row, should a store hold more than one — or None."""
    statement = sa.select(_runner_identity.c[_RUNNER_COLUMN]).order_by(_runner_identity.c.id.desc()).limit(1)
    return bind.execute(statement).scalar_one_or_none()


def _unstamped_stub(table: str, primary_key: tuple[str, ...]) -> sa.TableClause:
    return sa.table(
        table, *(cast(sa.ColumnClause[Any], sa.column(name)) for name in primary_key), sa.column(_RUNNER_COLUMN, sa.String)
    )


def _refuse_unowned(bind: sa.Connection) -> None:
    """Refuse, ahead of any change, while rows name no runner and the store has no identity to give them."""
    holding: list[str] = []
    for table, primary_key in _KEYED:
        stub = _unstamped_stub(table, primary_key)
        unowned = sa.select(sa.literal(1)).select_from(stub).where(stub.c[_RUNNER_COLUMN].is_(None)).limit(1)
        if bind.execute(unowned).first():
            holding.append(table)
    if holding:
        raise MigrationRefused(
            f"{', '.join(holding)} hold rows that name no runner and the store has no runner identity to name one; "
            "initialise the runner against its hub, or restore the pre-migrate backup"
        )


def _refuse_colliding(bind: sa.Connection, runner_id: str | None) -> None:
    """Refuse, ahead of any change, a store whose tables would hold two rows under a unique this revision adds once the
    rows lacking a runner are stamped with ``runner_id`` — a null ``hub_control`` row beside one the runner already owns,
    or two rows of one runner. Naming the table and the runner leaves the store as it was to be repaired."""
    for table, (name, columns) in _TIGHT_UNIQUES.items():
        if name in _unique_names(bind, table):
            continue
        stub = sa.table(table, *(sa.column(column, sa.String if column == _RUNNER_COLUMN else None) for column in columns))
        owner = sa.func.coalesce(stub.c[_RUNNER_COLUMN], runner_id) if runner_id else stub.c[_RUNNER_COLUMN]
        keys = [owner, *(stub.c[column] for column in columns if column != _RUNNER_COLUMN)]
        collision = sa.select(owner).select_from(stub).group_by(*keys).having(sa.func.count() > 1).limit(1)
        if (colliding := bind.execute(collision).first()) is not None:
            raise MigrationRefused(
                f"{table} would hold more than one row for runner {colliding[0]} once its rows are stamped, and "
                f"{name} allows one; remove the extra rows, or restore the pre-migrate backup"
            )


def _backfill(bind: sa.Connection, table: str, primary_key: tuple[str, ...], runner_id: str) -> None:
    """Stamp the rows still null in one pass along the primary key, at most ``_BATCH_ROWS`` per statement.

    Each page seeks past the last key the page before it stamped instead of scanning from the start, so the work grows
    with the row count, not its square.
    """
    stub = _unstamped_stub(table, primary_key)
    columns = [stub.c[name] for name in primary_key]
    key = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    unstamped = stub.c[_RUNNER_COLUMN].is_(None)
    page = sa.select(*columns).where(unstamped).order_by(*columns).limit(_BATCH_ROWS)
    after: sa.ColumnElement[bool] = sa.true()
    while rows := bind.execute(page.where(after)).all():
        last = tuple(rows[-1]) if len(columns) > 1 else rows[-1][0]
        bind.execute(sa.update(stub).where(unstamped, after, key <= last).values({_RUNNER_COLUMN: runner_id}))
        after = key > last


def _primary_key(bind: sa.Connection, table: str) -> tuple[str | None, tuple[str, ...]]:
    key = sa.inspect(bind).get_pk_constraint(table)
    return key.get("name"), tuple(key["constrained_columns"])


def _runner_nullable(bind: sa.Connection, table: str) -> bool:
    return any(c["nullable"] for c in sa.inspect(bind).get_columns(table) if c["name"] == _RUNNER_COLUMN)


def _unique_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(u["name"]) for u in sa.inspect(bind).get_unique_constraints(table) if u["name"]}


def _reflected(bind: sa.Connection, table: str, *, rekeyed: bool) -> sa.Table:
    """The table as the store holds it, the structure every batch copies from: the copy keeps each constraint it does
    not touch exactly as the store holds it. A table the batch rekeys has its old key's columns released, so the copy
    is not left with two keys for the new one to replace."""
    reflected = sa.Table(table, sa.MetaData(), autoload_with=bind)
    if rekeyed:
        for column in reflected.primary_key.columns:
            column.primary_key = False
    return reflected


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop a copy of ``table`` a batch left behind, under the name Alembic's batch gives it.

    The upgrade is one transaction from its first data write, so a stopped run rolls its copies back with the rest; this
    defends against a copy left by something outside that transaction. In a store with no identity row it is required:
    the first batch's table copy is created before any data write and commits alone.
    """
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _lane_table(table: str) -> sa.Table:
    """An outbox as a probe or a kept row names it: the columns either writes, ``seq`` as the key the parent's
    autoincrement issues."""
    return sa.Table(
        table,
        sa.MetaData(),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column(_RUNNER_COLUMN, sa.String),
        sa.Column("acked_at", UtcDateTime),
        sa.Column("kind", sa.String),
        sa.Column("segment_id", sa.String),
        sa.Column("chunk_id", sa.String),
        sa.Column("final", sa.Boolean),
        sa.Column("payload", sa.Text),
        sa.Column("created_at", UtcDateTime),
    )


def _probe(bind: sa.Connection, table: str, runner_id: str, values: dict[str, object]) -> int:
    """The seq the table's own autoincrement issues next: a row inserted and read back, then deleted."""
    stub = _lane_table(table)
    issued = bind.execute(sa.insert(stub).values({**values, _RUNNER_COLUMN: runner_id}))
    key = issued.inserted_primary_key
    assert key is not None  # the stub names ``seq`` as the key, so the driver answers the id it issued
    seq = int(key[0])
    bind.execute(sa.delete(stub).where(stub.c.seq == seq))
    return seq


def _seed_counters(bind: sa.Connection, runner_id: str) -> None:
    """Create ``outbox_counters`` and start each lane's counter at the greatest seq its outbox has issued."""
    if not sa.inspect(bind).has_table(_COUNTERS):
        _outbox_counters.create(bind)
    for lane, table, probe_values in _LANES:
        if _primary_key(bind, table)[1] != ("seq",):
            continue  # rebuilt by an earlier run: its counter was seeded before the rebuild
        issued = _probe(bind, table, runner_id, probe_values) - 1
        held = bind.execute(
            sa.select(_outbox_counters.c.last_seq).where(
                _outbox_counters.c.runner_id == runner_id, _outbox_counters.c.lane == lane
            )
        ).scalar_one_or_none()
        if held is None:
            bind.execute(sa.insert(_outbox_counters).values(runner_id=runner_id, lane=lane, last_seq=issued))
        elif held < issued:
            bind.execute(
                sa.update(_outbox_counters)
                .where(_outbox_counters.c.runner_id == runner_id, _outbox_counters.c.lane == lane)
                .values(last_seq=issued)
            )


def _sequence_before(bind: sa.Connection, table: str, *, autoincrement: bool = False) -> int | None:
    """The counter SQLite holds for an AUTOINCREMENT ``table`` ahead of a rebuild or a probe (0 for none); None off
    SQLite or for a table that is not AUTOINCREMENT (``autoincrement`` says the caller knows it is)."""
    if bind.dialect.name != "sqlite" or not (autoincrement or table in _AUTOINCREMENT):
        return None
    row = bind.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = :name"), {"name": table})
    return row.scalar_one_or_none() or 0


def _keep_sequence(bind: sa.Connection, table: str, before: int | None, *, exact: bool = False) -> None:
    """Leave ``table``'s SQLite counter at the greater of ``before`` and its greatest id: a rebuild restarts the counter
    at the greatest id the copy holds, which is 0 for an empty table, so ids it had issued would be issued again. The
    write follows the rebuild's own copy, so it is never the transaction's first data write. ``exact`` puts the counter
    back at ``before`` after a probe that consumed one id, so the next id issued is the one it would have been."""
    if before is None:
        return
    if exact:
        keep = before
    else:
        stub = sa.table(table, sa.column("id", sa.Integer))
        keep = max(before, bind.execute(sa.select(sa.func.max(stub.c.id))).scalar_one_or_none() or 0)
    update = bind.execute(sa.text("UPDATE sqlite_sequence SET seq = :seq WHERE name = :name"), {"seq": keep, "name": table})
    if not update.rowcount:
        bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES (:name, :seq)"), {"name": table, "seq": keep})


def _tighten(bind: sa.Connection, table: str) -> None:
    """One batch bringing ``table`` to its tight shape; nothing when it is already there."""
    key = _TIGHT_KEYS.get(table)
    unique = _TIGHT_UNIQUES.get(table)
    key_wanted = key is not None and _primary_key(bind, table)[1] != key[1]
    old_unique = _LOOSE_UNIQUES.get(table)
    drop_unique = old_unique is not None and old_unique[0] in _unique_names(bind, table)
    add_unique = unique is not None and unique[0] not in _unique_names(bind, table)
    nullable = _runner_nullable(bind, table)
    if not (key_wanted or drop_unique or add_unique or nullable):
        return
    _clear_stale_copy(bind, table)
    kwargs = {"sqlite_autoincrement": True} if table in _AUTOINCREMENT else {}
    copy_from = _reflected(bind, table, rekeyed=key_wanted)
    sequence = _sequence_before(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=copy_from, table_kwargs=kwargs) as batch:
        if nullable:
            batch.alter_column(_RUNNER_COLUMN, existing_type=sa.String(), nullable=False)
        if key_wanted and key is not None:
            current, _ = _primary_key(bind, table)
            if current:
                batch.drop_constraint(current, type_="primary")
            batch.create_primary_key(key[0], list(key[1]))
        if drop_unique and old_unique is not None:
            batch.drop_constraint(old_unique[0], type_="unique")
        if add_unique and unique is not None:
            batch.create_unique_constraint(unique[0], list(unique[1]))
    _keep_sequence(bind, table, sequence)


def upgrade() -> None:
    bind = op.get_bind()
    runner_id = _stored_runner_id(bind)
    _refuse_colliding(bind, runner_id)
    if runner_id is None:
        _refuse_unowned(bind)
    else:
        for table, primary_key in _KEYED:
            _backfill(bind, table, primary_key, runner_id)
        _seed_counters(bind, runner_id)
    if not sa.inspect(bind).has_table(_COUNTERS):
        _outbox_counters.create(bind)
    for table in _TABLES:
        _tighten(bind, table)


def _held_runners(bind: sa.Connection) -> list[str]:
    """Every runner the store holds a row for, in order."""
    held: set[str] = set(bind.execute(sa.select(_runner_identity.c[_RUNNER_COLUMN])).scalars())
    for table, _ in (*_KEYED, (_COUNTERS, ())):
        stub = sa.table(table, sa.column(_RUNNER_COLUMN, sa.String))
        held.update(
            runner for runner in bind.execute(sa.select(stub.c[_RUNNER_COLUMN]).distinct()).scalars() if runner
        )
    return sorted(held)


def _keep_last_issued(bind: sa.Connection, runner_id: str) -> dict[str, int]:
    """Leave every lane an acked row at its counter's last-issued seq, re-inserting it where retention pruned it;
    answer each lane's counter."""
    counters: dict[str, int] = {}
    for lane, table, probe_values in _LANES:
        last = bind.execute(
            sa.select(_outbox_counters.c.last_seq).where(
                _outbox_counters.c.runner_id == runner_id, _outbox_counters.c.lane == lane
            )
        ).scalar_one_or_none()
        if not last:
            continue
        counters[lane] = last
        stub = _lane_table(table)
        if bind.execute(sa.select(stub.c.seq).where(stub.c.seq == last)).first() is None:
            row = {**probe_values, "kind": "restored"} if lane == "outbound" else dict(probe_values)
            bind.execute(sa.insert(stub).values({**row, "seq": last, "acked_at": _EPOCH, _RUNNER_COLUMN: runner_id}))
    return counters


def _refuse_reused_seq(bind: sa.Connection, runner_id: str, counters: dict[str, int]) -> None:
    """Refuse while a restored outbox would issue a seq at or below its lane's counter — a probe the outbox refuses
    because a row already holds the seq it drew included, since the counter, not the outbox's own sequence, issued
    every seq since the upgrade."""
    for lane, table, probe_values in _LANES:
        if lane not in counters:
            continue
        before = _sequence_before(bind, table, autoincrement=True)
        try:
            probed: int | str = _probe(bind, table, runner_id, probe_values)
        except IntegrityError as held:
            raise MigrationRefused(_reused_seq_refusal(table, "a seq a row already holds", counters[lane])) from held
        _keep_sequence(bind, table, before, exact=True)
        if probed <= counters[lane]:
            raise MigrationRefused(_reused_seq_refusal(table, f"seq {probed}", counters[lane]))


def _reused_seq_refusal(table: str, issued: str, counter: int) -> str:
    return (
        f"the restored {table} would issue {issued}, at or below the {counter} already issued; "
        "restore the pre-migrate backup instead (docs/upgrade.md)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    runners = _held_runners(bind)
    if len(runners) > 1:
        raise MigrationRefused(f"remove every runner but one before downgrading; the store holds {', '.join(runners)}")
    counters = _keep_last_issued(bind, runners[0]) if runners else {}
    for table in reversed(_TABLES):
        _loosen(bind, table)
    if runners:
        _refuse_reused_seq(bind, runners[0], counters)
    if sa.inspect(bind).has_table(_COUNTERS):
        op.drop_table(_COUNTERS)


def _loosen(bind: sa.Connection, table: str) -> None:
    """One batch restoring ``table`` to the parent's keys and nullable ``runner_id``; nothing when it is already there."""
    loose = _LOOSE_KEYS.get(table)
    key_wanted = loose is not None and _primary_key(bind, table)[1] != loose[1]
    tight_unique = _TIGHT_UNIQUES.get(table)
    drop_unique = tight_unique is not None and tight_unique[0] in _unique_names(bind, table)
    old_unique = _LOOSE_UNIQUES.get(table)
    add_unique = old_unique is not None and old_unique[0] not in _unique_names(bind, table)
    nullable = not _runner_nullable(bind, table) and table != "runner_identity"
    if not (key_wanted or drop_unique or add_unique or nullable):
        return
    _clear_stale_copy(bind, table)
    # The outboxes and ``in_flight_elicitations`` were AUTOINCREMENT before; the rebuild names it again, or ids repeat.
    kwargs = {"sqlite_autoincrement": True} if table in _AUTOINCREMENT | _OUTBOXES else {}
    copy_from = _reflected(bind, table, rekeyed=key_wanted)
    sequence = _sequence_before(bind, table)
    with op.batch_alter_table(table, recreate="auto", copy_from=copy_from, table_kwargs=kwargs) as batch:
        # The key goes back first: an in-place ALTER refuses a nullable column while a primary key still names it.
        if key_wanted and loose is not None:
            current, _ = _primary_key(bind, table)
            if current:
                batch.drop_constraint(current, type_="primary")
            batch.create_primary_key(loose[0], list(loose[1]))
        if nullable:
            batch.alter_column(_RUNNER_COLUMN, existing_type=sa.String(), nullable=True)
        if drop_unique and tight_unique is not None:
            batch.drop_constraint(tight_unique[0], type_="unique")
        if add_unique and old_unique is not None:
            batch.create_unique_constraint(old_unique[0], list(old_unique[1]))
    _keep_sequence(bind, table, sequence)
