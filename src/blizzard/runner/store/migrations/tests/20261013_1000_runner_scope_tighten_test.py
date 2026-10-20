"""The runner-scope tightening revision makes ``runner_id`` required, keys the outboxes and ``route_tokens`` by runner,
and moves both outboxes onto per-runner counters seeded from the autoincrement they leave. Upgraded from stores built
at the additive revision — held, parked, and escalated leases, an outbox backlog, a lane whose newest seq retention
pruned — every counter starts at or above every seq the store issued and the invariant checker holds. A store with
rows and no identity refuses the upgrade; the downgrade restores the parent's schema, keeps each lane's last-issued seq
as an acked row, and refuses a store of two runners. A rerun of an upgrade that already ran changes nothing.

Stores are seeded from the shapes reflected at the parent revision, never from ``src/blizzard/runner/store/schema.py``,
whose tables keep changing shape after this revision."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.tools.invariants import RunnerInvariants
from tests.config_inputs import init_host
from tests.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.runner_scope_seeding import KEYED, T0, reflect, seed_every_keyed_table

_PARENT = "20261011_1000_runner_scope"

_REVISION = "20261013_1000_runner_scope_tighten"

_RUNNER_ID = "rn_01JX0000000000000000000000"

_OTHER_RUNNER_ID = "rn_01JX0000000000000000000001"

_OUTBOXES = {"outbound": "outbound_buffer", "transcript": "transcript_outbound_buffer"}


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_identity(engine: sa.Engine, runner_id: str = _RUNNER_ID) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (:id, 'r', :at)"),
            {"id": runner_id, "at": T0.isoformat()},
        )


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _row_counts(engine: sa.Engine, tables: set[str]) -> dict[str, int]:
    with engine.connect() as conn:
        return {name: conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one() for name in tables}


def _schema(engine: sa.Engine) -> dict[str, object]:
    """Each table's columns, key columns, indexes, unique constraints, checks, and foreign keys. A primary key's name
    is left out: SQLite keeps none for an inline key, and the one the downgrade gives a rebuilt composite table
    (``route_tokens``) is the name a backend would give it unnamed."""
    inspector = sa.inspect(engine)
    return {
        table: (
            tuple((c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)),
            tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset((tuple(u["column_names"]), u["name"]) for u in inspector.get_unique_constraints(table)),
            frozenset((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(table)),
            frozenset(
                (tuple(fk["constrained_columns"]), fk["referred_table"], fk["name"])
                for fk in inspector.get_foreign_keys(table)
            ),
        )
        for table in _tables(engine)
    }


def _autoincrement_tables(engine: sa.Engine) -> set[str]:
    """The tables whose DDL names AUTOINCREMENT — which the inspector cannot see."""
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all()
    return {name for name, ddl in rows if ddl and "AUTOINCREMENT" in ddl.upper()}


def _counters(engine: sa.Engine) -> dict[tuple[str, str], int]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT runner_id, lane, last_seq FROM outbox_counters")).all()
    return {(runner_id, lane): last_seq for runner_id, lane, last_seq in rows}


def _seqs(engine: sa.Engine, table: str) -> list[int]:
    with engine.connect() as conn:
        return list(conn.execute(sa.text(f"SELECT seq FROM {table} ORDER BY seq")).scalars())


def _issue_elicitation(engine: sa.Engine) -> int:
    """Insert an in-flight elicitation and answer the id the table's autoincrement issued it."""
    with engine.begin() as conn:
        return conn.execute(
            sa.text(
                "INSERT INTO in_flight_elicitations (lease_id, epoch, output_path, first_launched_at, relaunch_count, "
                "runner_id) VALUES ('l', 1, '/o', :at, 0, :runner)"
            ),
            {"at": T0.isoformat(), "runner": _RUNNER_ID},
        ).lastrowid


def _sequence(engine: sa.Engine) -> int | None:
    with engine.connect() as conn:
        return conn.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name = 'in_flight_elicitations'")).scalar()


def _store_with_pruned_elicitations(tmp_path: Path, *, issued: int) -> tuple[MigrationRunner, sa.Engine, int]:
    """A parent-revision store that issued ``issued`` elicitation ids and then deleted the newest, so the counter is
    above every id the table still holds; answers the counter."""
    migration, engine = _store_at_parent(tmp_path)
    _seed_identity(engine)
    ids = [_issue_elicitation(engine) for _ in range(issued)]
    if ids:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM in_flight_elicitations WHERE id = :id"), {"id": ids[-1]})
    return migration, engine, (ids[-1] if ids else 0)


@dataclass(frozen=True)
class _Lane:
    """What retention and the drain left in one outbox: ``acked`` rows delivered, ``pending`` rows waiting, and
    ``pruned`` newest rows pruned after they were acked — all issued, in that order."""

    acked: int
    pending: int
    pruned: int

    @property
    def issued(self) -> int:
        return self.acked + self.pending + self.pruned


@dataclass(frozen=True)
class _Fixture:
    outbound: _Lane
    transcript: _Lane


_FIXTURES = {
    "backlog": _Fixture(_Lane(acked=2, pending=3, pruned=0), _Lane(acked=0, pending=2, pruned=0)),
    "newest-pruned": _Fixture(_Lane(acked=2, pending=0, pruned=3), _Lane(acked=1, pending=0, pruned=2)),
    "every-row-pruned": _Fixture(_Lane(acked=0, pending=0, pruned=4), _Lane(acked=0, pending=0, pruned=3)),
    "empty": _Fixture(_Lane(0, 0, 0), _Lane(0, 0, 0)),
}


def _seed_leases(engine: sa.Engine, shapes: sa.MetaData) -> None:
    """A held lease with its binding, a parked lease, and an escalated lease, as the previous release wrote them:
    alternately naming no runner (as its statements do after the additive revision) and the runner."""
    leases, context, bindings = shapes.tables["leases"], shapes.tables["lease_context"], shapes.tables["env_bindings"]
    parks, closures = shapes.tables["park_facts"], shapes.tables["lease_closures"]
    with engine.begin() as conn:
        for index, (lease_id, chunk_id) in enumerate((("lease_held", "ch_held"), ("lease_parked", "ch_parked"))):
            owner = {"runner_id": _RUNNER_ID} if index % 2 else {}
            conn.execute(
                sa.insert(leases).values(lease_id=lease_id, chunk_id=chunk_id, epoch=1, created_at=T0, **owner)
            )
            conn.execute(
                sa.insert(context).values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    retries_max=1,
                    recorded_at=T0,
                    **owner,
                )
            )
        conn.execute(
            sa.insert(bindings).values(
                chunk_id="ch_held", environment_id="env_1", workdir="/w", bound_at=T0, runner_id=_RUNNER_ID
            )
        )
        conn.execute(
            sa.insert(parks).values(lease_id="lease_parked", chunk_id="ch_parked", question_id="qn_1", parked_at=T0)
        )
        conn.execute(sa.insert(leases).values(lease_id="lease_escalated", chunk_id="ch_esc", epoch=1, created_at=T0))
        conn.execute(
            sa.insert(closures).values(
                lease_id="lease_escalated",
                chunk_id="ch_esc",
                node_id="nd_build",
                reason="escalated",
                closed_at=T0,
                escalation_cause="retries_exhausted",
            )
        )


def _seed_lane(engine: sa.Engine, table: sa.Table, lane: _Lane) -> None:
    """Issue ``lane``'s rows through the table's own autoincrement, then prune its newest ``pruned`` rows."""
    columns = {c.name for c in table.columns}
    with engine.begin() as conn:
        for index in range(lane.issued):
            values: dict[str, object] = {"payload": "{}", "created_at": T0}
            if "kind" in columns:
                values["kind"] = "lease.minted"
            if "final" in columns:
                values |= {"final": False, "segment_id": "seg_1", "chunk_id": "ch_held"}
            if index < lane.acked:
                values["acked_at"] = T0
            conn.execute(sa.insert(table).values(values))
        if lane.pruned:
            keep = lane.acked + lane.pending
            conn.execute(sa.delete(table).where(table.c.seq > keep))


def _store_with(tmp_path: Path, fixture: _Fixture) -> tuple[MigrationRunner, sa.Engine]:
    migration, engine = _store_at_parent(tmp_path)
    _seed_identity(engine)
    shapes = reflect(engine)
    _seed_leases(engine, shapes)
    _seed_lane(engine, shapes.tables["outbound_buffer"], fixture.outbound)
    _seed_lane(engine, shapes.tables["transcript_outbound_buffer"], fixture.transcript)
    return migration, engine


def _stop_before_batch(monkeypatch: pytest.MonkeyPatch, table: str) -> None:
    """Make the upgrade's batch step for ``table`` raise, as a process stopped at that point would."""
    real = Operations.batch_alter_table

    def batch_alter_table(self: Operations, name: str, *args: object, **kwargs: object) -> object:
        if name == table:
            raise KeyboardInterrupt(f"stopped before {table}")
        return real(self, name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Operations, "batch_alter_table", batch_alter_table)


_KILL_AT_FIRST_INSERT = """
import os, sys
import sqlalchemy as sa
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.store import MIGRATIONS_DIR

def die(conn, cursor, statement, *args):
    if statement.lstrip().startswith("INSERT INTO _alembic_tmp_"):
        os._exit(7)

sa.event.listen(sa.engine.Engine, "before_cursor_execute", die)
MigrationRunner(script_location=MIGRATIONS_DIR, url=sys.argv[1]).upgrade(sys.argv[2])
"""


def _revision_module() -> ModuleType:
    path = Path(MIGRATIONS_DIR) / "versions" / f"{_REVISION}.py"
    spec = importlib.util.spec_from_file_location("runner_scope_tighten", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _restored_outbox() -> sa.Engine:
    """A store holding the parent's outboxes, empty: their autoincrement issues ``seq`` 1 next."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE outbound_buffer (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind VARCHAR NOT NULL,"
                " chunk_id VARCHAR, lease_id VARCHAR, payload TEXT NOT NULL, created_at DATETIME NOT NULL,"
                " acked_at DATETIME, runner_id VARCHAR)"
            )
        )
        conn.execute(
            sa.text(
                "CREATE TABLE transcript_outbound_buffer (seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " segment_id VARCHAR NOT NULL, chunk_id VARCHAR NOT NULL, final BOOLEAN NOT NULL,"
                " payload TEXT NOT NULL, created_at DATETIME NOT NULL, acked_at DATETIME, runner_id VARCHAR)"
            )
        )
    return engine


@pytest.mark.component
@pytest.mark.parametrize("name", list(_FIXTURES))
def test_a_store_upgrades_with_every_counter_at_or_above_every_seq_it_issued(tmp_path: Path, name: str) -> None:
    fixture = _FIXTURES[name]
    migration, engine = _store_with(tmp_path, fixture)
    try:
        retained = {table: _seqs(engine, table) for table in _OUTBOXES.values()}
        held = {table: _row_counts(engine, {table})[table] for table in KEYED}

        migration.upgrade(_REVISION)

        assert _counters(engine) == {
            (_RUNNER_ID, "outbound"): fixture.outbound.issued,
            (_RUNNER_ID, "transcript"): fixture.transcript.issued,
        }
        assert {table: _seqs(engine, table) for table in _OUTBOXES.values()} == retained
        assert _row_counts(engine, set(held)) == held
        with engine.connect() as conn:
            for table in KEYED:
                owners = set(conn.execute(sa.text(f"SELECT DISTINCT runner_id FROM {table}")).scalars())
                assert owners <= {_RUNNER_ID}, table
        assert RunnerInvariants(engine).run() == []

        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER_ID)
        issued = store.enqueue_outbound(kind="lease.minted", chunk_id=None, lease_id=None, payload="{}", created_at=T0)
        assert issued == fixture.outbound.issued + 1
        assert RunnerInvariants(engine).run() == []
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_upgrade_tightens_the_keys_and_leaves_only_in_flight_elicitations_autoincrementing(tmp_path: Path) -> None:
    migration, engine = _store_with(tmp_path, _FIXTURES["backlog"])
    try:
        migration.upgrade(_REVISION)

        inspector = sa.inspect(engine)
        for table in KEYED | {"runner_identity"}:
            [runner_id] = [c for c in inspector.get_columns(table) if c["name"] == "runner_id"]
            assert not runner_id["nullable"], table
        keys = {t: tuple(inspector.get_pk_constraint(t)["constrained_columns"]) for t in _tables(engine)}
        assert keys["outbound_buffer"] == ("runner_id", "seq")
        assert keys["transcript_outbound_buffer"] == ("runner_id", "seq")
        assert keys["route_tokens"] == ("runner_id", "chunk_id")
        assert keys["outbox_counters"] == ("runner_id", "lane")
        uniques = {
            (table, tuple(u["column_names"]))
            for table in _tables(engine)
            for u in inspector.get_unique_constraints(table)
        }
        assert {
            ("runner_identity", ("runner_id",)),
            ("hub_control", ("runner_id",)),
            ("hub_served_projects", ("runner_id", "position")),
        } <= uniques
        assert ("hub_served_projects", ("position",)) not in uniques
        assert "in_flight_elicitations" in _autoincrement_tables(engine)
        assert not _autoincrement_tables(engine) & set(_OUTBOXES.values())
    finally:
        engine.dispose()


@pytest.mark.component
@pytest.mark.parametrize("issued", [0, 3])
def test_an_elicitation_id_issued_before_the_upgrade_is_never_issued_again(tmp_path: Path, issued: int) -> None:
    migration, engine, high_water = _store_with_pruned_elicitations(tmp_path, issued=issued)
    try:
        migration.upgrade(_REVISION)

        assert (_sequence(engine) or 0) >= high_water
        assert _issue_elicitation(engine) > high_water
    finally:
        engine.dispose()


@pytest.mark.component
@pytest.mark.parametrize("issued", [0, 3])
def test_an_elicitation_id_issued_before_a_down_and_up_round_trip_is_never_issued_again(
    tmp_path: Path, issued: int
) -> None:
    migration, engine, high_water = _store_with_pruned_elicitations(tmp_path, issued=issued)
    try:
        migration.upgrade(_REVISION)
        newest = _issue_elicitation(engine)
        migration.downgrade(_PARENT)
        assert (_sequence(engine) or 0) >= newest
        migration.upgrade(_REVISION)

        assert newest > high_water
        assert (_sequence(engine) or 0) >= newest
        assert _issue_elicitation(engine) > newest
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_upgrade_refuses_a_store_with_rows_and_no_identity_and_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        seed_every_keyed_table(engine, reflect(engine), omit=frozenset({"runner_id"}))
        schema, counts = _schema(engine), _row_counts(engine, _tables(engine))

        with pytest.raises(RuntimeError, match="name no runner"):
            migration.upgrade(_REVISION)

        assert migration.current_revision() == _PARENT
        assert _schema(engine) == schema
        assert _row_counts(engine, _tables(engine)) == counts
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_cli_shows_a_refused_upgrade_as_an_error_without_a_traceback(tmp_path: Path) -> None:
    """A revision's refusal is the operator's error, in the refusal's own words."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        seed_every_keyed_table(engine, reflect(engine), omit=frozenset({"runner_id"}))

        upgrade = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(tmp_path)])

        assert upgrade.exit_code != 0
        assert "name no runner" in upgrade.output
        assert "Traceback" not in upgrade.output
        assert migration.current_revision() == _PARENT
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_cli_shows_a_refused_downgrade_as_an_error_without_a_traceback(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        migration.upgrade(_REVISION)
        _seed_identity(engine, _OTHER_RUNNER_ID)

        downgrade = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(tmp_path), "--down", _PARENT])

        assert downgrade.exit_code != 0
        assert "remove every runner but one" in downgrade.output
        assert "Traceback" not in downgrade.output
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_upgrade_refuses_rows_that_would_collide_under_a_new_unique_naming_the_table_and_runner(
    tmp_path: Path,
) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        with engine.begin() as conn:
            for owner in (None, _RUNNER_ID):
                conn.execute(
                    sa.text("INSERT INTO hub_control (paused, updated_at, runner_id) VALUES (0, :at, :owner)"),
                    {"at": T0.isoformat(), "owner": owner},
                )
        schema, counts = _schema(engine), _row_counts(engine, _tables(engine))

        with pytest.raises(RuntimeError, match=f"hub_control would hold more than one row for runner {_RUNNER_ID}"):
            migration.upgrade(_REVISION)

        assert migration.current_revision() == _PARENT
        assert _schema(engine) == schema
        assert _row_counts(engine, _tables(engine)) == counts
    finally:
        engine.dispose()


@pytest.mark.component
def test_a_store_with_no_identity_and_no_rows_upgrades_and_registers_no_counter(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _counters(engine) == {}
    finally:
        engine.dispose()


@pytest.mark.component
def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_with(tmp_path, _FIXTURES["newest-pruned"])
    try:
        migration.upgrade(_REVISION)
        schema, counters, counts = _schema(engine), _counters(engine), _row_counts(engine, _tables(engine))
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert (_schema(engine), _counters(engine), _row_counts(engine, _tables(engine))) == (schema, counters, counts)
    finally:
        engine.dispose()


@pytest.mark.component
def test_an_upgrade_stopped_after_an_outbox_rebuild_leaves_the_store_at_the_parent_and_a_rerun_finishes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The upgrade is one transaction from its first data write: a run stopped after the outbox rebuilds rolls back
    the rebuilds and the counters with them, leaving the parent's keys and nullability; only the DDL before that
    first write (the empty ``outbox_counters``) may survive, and the rerun runs over it to the same end state."""
    fixture = _FIXTURES["newest-pruned"]
    migration, engine = _store_with(tmp_path, fixture)
    try:
        retained = {table: _seqs(engine, table) for table in _OUTBOXES.values()}
        before = _schema(engine)
        with monkeypatch.context() as patch:
            _stop_before_batch(patch, "transcript_segments")
            with pytest.raises(KeyboardInterrupt):
                migration.upgrade(_REVISION)

        assert migration.current_revision() == _PARENT
        inspector = sa.inspect(engine)
        for table in _OUTBOXES.values():
            assert tuple(inspector.get_pk_constraint(table)["constrained_columns"]) == ("seq",)
            assert {c["name"]: c["nullable"] for c in inspector.get_columns(table)}["runner_id"] is True
        assert not {name for name in _tables(engine) if name.startswith("_alembic_tmp_")}
        assert {k: v for k, v in _schema(engine).items() if k != "outbox_counters"} == before
        if "outbox_counters" in _tables(engine):
            assert _counters(engine) == {}
        assert {table: _seqs(engine, table) for table in _OUTBOXES.values()} == retained

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _counters(engine) == {
            (_RUNNER_ID, "outbound"): fixture.outbound.issued,
            (_RUNNER_ID, "transcript"): fixture.transcript.issued,
        }
        assert {table: _seqs(engine, table) for table in _OUTBOXES.values()} == retained
        assert RunnerInvariants(engine).run() == []
    finally:
        engine.dispose()


@pytest.mark.component
def test_an_upgrade_of_a_store_with_no_identity_killed_between_a_table_copy_and_its_insert_is_finished_by_a_rerun(
    tmp_path: Path,
) -> None:
    """With no identity row there is no data write ahead of the first batch, so its ``_alembic_tmp_*`` table commits
    alone and survives a killed process; the rerun clears it rather than failing on a table that exists."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        killed = subprocess.run(
            [sys.executable, "-c", _KILL_AT_FIRST_INSERT, str(engine.url), _REVISION],
            capture_output=True,
            check=False,
            timeout=300,
        )

        assert killed.returncode == 7, killed.stderr
        assert migration.current_revision() == _PARENT
        assert {name for name in _tables(engine) if name.startswith("_alembic_tmp_")}

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert not {name for name in _tables(engine) if name.startswith("_alembic_tmp_")}
        assert RunnerInvariants(engine).run() == []
    finally:
        engine.dispose()


@pytest.mark.component
def test_a_rerun_over_the_empty_counter_table_a_stopped_upgrade_left_finishes_it(tmp_path: Path) -> None:
    """The one state a stopped run can keep: ``outbox_counters`` created by DDL before the upgrade's first data write,
    empty. The rerun seeds into it rather than creating it again."""
    fixture = _FIXTURES["newest-pruned"]
    migration, engine = _store_with(tmp_path, fixture)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE outbox_counters (runner_id VARCHAR NOT NULL, lane VARCHAR NOT NULL,"
                    " last_seq INTEGER NOT NULL, CONSTRAINT pk_outbox_counters PRIMARY KEY (runner_id, lane))"
                )
            )
        retained = {table: _seqs(engine, table) for table in _OUTBOXES.values()}

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _counters(engine) == {
            (_RUNNER_ID, "outbound"): fixture.outbound.issued,
            (_RUNNER_ID, "transcript"): fixture.transcript.issued,
        }
        assert {table: _seqs(engine, table) for table in _OUTBOXES.values()} == retained
        assert RunnerInvariants(engine).run() == []
    finally:
        engine.dispose()


@pytest.mark.component
@pytest.mark.parametrize("name", list(_FIXTURES))
def test_the_downgrade_restores_the_parent_schema_and_keeps_each_lanes_last_issued_seq(
    tmp_path: Path, name: str
) -> None:
    fixture = _FIXTURES[name]
    migration, engine = _store_with(tmp_path, fixture)
    try:
        schema, autoincrement = _schema(engine), _autoincrement_tables(engine)
        retained = {table: _seqs(engine, table) for table in _OUTBOXES.values()}
        others = {
            table: n for table, n in _row_counts(engine, _tables(engine)).items() if table not in _OUTBOXES.values()
        }

        migration.upgrade(_REVISION)
        migration.downgrade(_PARENT)

        assert migration.current_revision() == _PARENT
        assert _schema(engine) == schema
        assert _autoincrement_tables(engine) == autoincrement
        assert {
            table: n for table, n in _row_counts(engine, _tables(engine)).items() if table not in _OUTBOXES.values()
        } == others
        for lane, table in _OUTBOXES.items():
            issued = getattr(fixture, lane).issued
            kept = retained[table] if not issued or issued in retained[table] else [*retained[table], issued]
            assert _seqs(engine, table) == kept
            if issued and issued not in retained[table]:
                with engine.connect() as conn:
                    acked = conn.execute(
                        sa.text(f"SELECT acked_at FROM {table} WHERE seq = :seq"), {"seq": issued}
                    ).scalar_one()
                assert acked is not None
        with engine.begin() as conn:
            probed = conn.execute(
                sa.text("INSERT INTO outbound_buffer (kind, payload, created_at) VALUES ('k', '{}', :at)"),
                {"at": T0.isoformat()},
            ).lastrowid
        assert probed == fixture.outbound.issued + 1
    finally:
        engine.dispose()


@pytest.mark.component
def test_the_downgrade_refuses_a_store_of_two_runners_naming_both_and_changes_nothing(tmp_path: Path) -> None:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_REVISION)
    engine = create_engine_from_url(config.db_url)
    try:
        _seed_identity(engine)
        _seed_identity(engine, _OTHER_RUNNER_ID)
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO hub_control (paused, updated_at, runner_id) VALUES (0, :at, :id)"),
                {"at": T0.isoformat(), "id": _OTHER_RUNNER_ID},
            )
        schema, counts = _schema(engine), _row_counts(engine, _tables(engine))

        with pytest.raises(RuntimeError, match="remove every runner but one") as refused:
            migration.downgrade(_PARENT)

        assert _RUNNER_ID in str(refused.value)
        assert _OTHER_RUNNER_ID in str(refused.value)
        assert migration.current_revision() == _REVISION
        assert _schema(engine) == schema
        assert _row_counts(engine, _tables(engine)) == counts
    finally:
        engine.dispose()


@pytest.mark.unit
@pytest.mark.parametrize(("counter", "refused"), [(0, False), (1, True), (5, True)])
def test_a_probe_at_or_below_the_counter_refuses_the_downgrade(counter: int, refused: bool) -> None:
    """A restored outbox whose first seq is 1 reaches at most a counter of 0: at or above 1 it would reissue a seq."""
    engine = _restored_outbox()
    try:
        with engine.begin() as conn:
            if refused:
                with pytest.raises(RuntimeError, match=rf"would issue seq 1, at or below the {counter} already issued"):
                    _revision_module()._refuse_reused_seq(conn, _RUNNER_ID, {"outbound": counter})
            else:
                _revision_module()._refuse_reused_seq(conn, _RUNNER_ID, {"outbound": counter})
    finally:
        engine.dispose()


@pytest.mark.unit
def test_a_lane_with_no_counter_is_not_probed() -> None:
    engine = _restored_outbox()
    try:
        with engine.begin() as conn:
            _revision_module()._refuse_reused_seq(conn, _RUNNER_ID, {})
            assert conn.execute(sa.text("SELECT count(*) FROM outbound_buffer")).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.unit
def test_a_probe_drawing_a_seq_a_row_already_holds_refuses_the_downgrade_naming_the_backup() -> None:
    """Where the restored sequence never saw the seqs the counter issued, as on Postgres, the probe draws one a row
    still holds and the insert fails: that is the refusal, naming the backup, not a bare integrity error. SQLite's
    autoincrement never draws a held seq, so a trigger stands in for the collision."""
    engine = _restored_outbox()
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TRIGGER held BEFORE INSERT ON outbound_buffer"
                    " BEGIN SELECT RAISE(ABORT, 'UNIQUE constraint failed: outbound_buffer.seq'); END"
                )
            )
            with pytest.raises(
                RuntimeError,
                match=r"would issue a seq a row already holds, at or below the 3 already issued; "
                r"restore the pre-migrate backup",
            ):
                _revision_module()._refuse_reused_seq(conn, _RUNNER_ID, {"outbound": 3})
    finally:
        engine.dispose()
