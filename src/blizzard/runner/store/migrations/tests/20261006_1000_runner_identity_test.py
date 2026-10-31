"""Runner-store migrations: the store keys nothing on the runner's id and keeps one identity row."""

# pyright: standard

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.runner.tracing.replay import LeaseTraceReplay, ReplayUnavailable
from blizzard.runner.tracing.sweep import LeaseTraceSweep
from tests.config_inputs import init_host
from tests.migration_support_runner_store_migrations import (
    _LATER,
    _NEW,
    _RUNNER,
    _columns,
    _keyed,
    _leases,
    _rows,
    _seed_identity_before_the_scope,
)
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_store_errors
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter, upgrade_runner_store_as_registered

pytestmark = [pytest.mark.component]

_IDENTITY_PARENT = "20261005_1000_runner_credential_renewal_facts"

_IDENTITY = "20261006_1000_runner_identity"

_KEYED_TABLES = ("hub_control", "local_pause_facts", "daemon_liveness", "leases")

_OLD = datetime(2026, 9, 1, 12, tzinfo=UTC)

_hub_control = sa.Table(
    "hub_control",
    _keyed,
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("paused", sa.Boolean()),
    sa.Column("updated_at", UtcDateTime()),
)

_daemon_liveness = sa.Table(
    "daemon_liveness",
    _keyed,
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("alive_at", UtcDateTime()),
)

_local_pause_facts = sa.Table(
    "local_pause_facts",
    _keyed,
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("runner_id", sa.String()),
    sa.Column("paused", sa.Boolean()),
    sa.Column("set_at", UtcDateTime()),
    sa.Column("set_by", sa.String()),
    sa.Column("reason", sa.String()),
)

_liveness_unscoped = sa.table("daemon_liveness", sa.column("alive_at", UtcDateTime()))


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_IDENTITY_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_a_store_spanning_a_rename(engine: sa.Engine) -> None:
    """``runner-local`` (paused at the hub) renamed to ``r-claude`` (running): two ids in one store."""
    with engine.begin() as conn:
        conn.execute(
            _hub_control.insert(),
            [
                {"runner_id": "runner-local", "paused": True, "updated_at": _OLD},
                {"runner_id": "r-claude", "paused": False, "updated_at": _NEW},
            ],
        )
        conn.execute(
            _daemon_liveness.insert(),
            [{"runner_id": "runner-local", "alive_at": _OLD}, {"runner_id": "r-claude", "alive_at": _NEW}],
        )
        conn.execute(
            _local_pause_facts.insert(),
            [
                {"runner_id": "runner-local", "paused": True, "set_at": _OLD, "set_by": "operator"},
                {"runner_id": "r-claude", "paused": False, "set_at": _NEW, "set_by": "operator"},
            ],
        )
        conn.execute(
            _leases.insert(),
            [
                {"lease_id": "l_old", "chunk_id": "ch_1", "epoch": 1, "runner_id": "runner-local", "created_at": _OLD},
                {"lease_id": "l_new", "chunk_id": "ch_2", "epoch": 1, "runner_id": "r-claude", "created_at": _NEW},
            ],
        )


_CEILING = "spend ceiling: $50.00 over 24h"

# The runner store's revision before it kept an identity row, when every lease carried the runner's name.
_PRE_IDENTITY = "20261005_1000_runner_credential_renewal_facts"

_MINTED = "2026-10-05 09:00:00"

_CLOSED = "2026-10-05 09:05:00"

_CLOSED_AT = datetime(2026, 10, 5, 9, 5, tzinfo=UTC)

_CONFIG = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600, replay_max_window=3600)

_REGISTERED = RunnerIdentity("rn_01JZ8Q4V6XK3M2N5P7R9T1W3Y5", "r-claude", datetime(2026, 10, 6, 12, 0, tzinfo=UTC))

_RENAMED = RunnerIdentity(_REGISTERED.runner_id, "r-claude-2", datetime(2026, 10, 6, 13, 0, tzinfo=UTC))


def _pre_identity_store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    """One lease minted and closed under the old shape, keyed by the runner's configured name, then
    the real migration to head with the runner registered in between — which keeps the lease and claims it for that
    runner. The store holds an identity row, but the process holder has not been told of it."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    migrations.upgrade(_PRE_IDENTITY)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            for statement in (
                "INSERT INTO leases (lease_id, chunk_id, epoch, runner_id, pid, process_start_time, session_id,"
                " harness_id, created_at) VALUES ('lease_old', 'ch_old', 1, 'runner-local', 200, 'start-200',"
                " 'sess-old', 'claude-code', :minted)",
                "INSERT INTO lease_context (lease_id, chunk_id, graph_id, node_id, node_name, retries_max,"
                " recorded_at) VALUES ('lease_old', 'ch_old', 'gr_1', 'nd_build', 'build', 2, :minted)",
                "INSERT INTO lease_spawns (lease_id, spawned_at, harness_id, pid, process_start_time, session_id,"
                " identified_at) VALUES ('lease_old', :minted, 'claude-code', 200, 'start-200', 'sess-old', :minted)",
                "INSERT INTO lease_closures (lease_id, chunk_id, node_id, reason, closed_at)"
                " VALUES ('lease_old', 'ch_old', 'nd_build', 'transitioned', :closed)",
            ):
                conn.execute(sa.text(statement), {"minted": _MINTED, "closed": _CLOSED})
    finally:
        engine.dispose()
    upgrade_runner_store_as_registered(migrations, url, _REGISTERED.runner_id)
    return SqlAlchemyRunnerStore(create_engine_from_url(url), runner_store_errors(), runner_id=_REGISTERED.runner_id)


def _replayed(store: SqlAlchemyRunnerStore, identity: RunnerIdentityHolder) -> list[FinishedSpan]:
    exporter = InMemoryTraceExporter()
    replay = LeaseTraceReplay(
        leases=store,
        exporter=exporter,
        identity=identity,
        clock=FixedClock(_CLOSED_AT + timedelta(minutes=10)),
        config=_CONFIG,
    )
    replay.replay(_CLOSED_AT - timedelta(minutes=10), _CLOSED_AT + timedelta(minutes=10), dry_run=False)
    return [span for batch in exporter.batches for span in batch]


def _runners(spans: list[FinishedSpan]) -> set[tuple[object, object]]:
    return {(span.attributes.get(RUNNER_ID), span.attributes.get(RUNNER_NAME)) for span in spans}


def _register(store: SqlAlchemyRunnerStore, identity: RunnerIdentityHolder, registered: RunnerIdentity) -> None:
    """What a successful registration leaves: the row first, then the process's holder."""
    store.record_runner_identity(registered)
    identity.hold(registered)


def _cursor_rows(store: SqlAlchemyRunnerStore) -> int:
    with store._engine.connect() as conn:
        return conn.exec_driver_sql("SELECT count(*) FROM trace_cursor").scalar_one()


def test_identity_migration_keeps_the_newest_liveness_ids_singletons_and_every_history_row(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_store_spanning_a_rename(engine)
        migration.upgrade(_IDENTITY)

        for table in _KEYED_TABLES:
            assert "runner_id" not in _columns(engine, table)
        _seed_identity_before_the_scope(migration, engine)  # the identity migration itself holds no identity
        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        assert _rows(engine, "SELECT count(*) FROM hub_control") == [(1,)]
        assert store.hub_paused() is False  # r-claude's mirror, not the older id's pause
        assert store.hub_contact_at() == _NEW
        assert _rows(engine, "SELECT count(*) FROM daemon_liveness") == [(1,)]
        assert store.last_daemon_liveness() == _NEW
        assert _rows(engine, "SELECT paused FROM local_pause_facts ORDER BY id") == [(1,), (0,)]
        assert _rows(engine, "SELECT lease_id FROM leases ORDER BY lease_id") == [("l_new",), ("l_old",)]
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("facts", "paused", "reason"),
    [
        ([("runner-local", True, _NEW, "usage limit: claude_code")], False, None),
        ([("r-claude", True, _OLD, _CEILING), ("runner-local", False, _NEW, None)], True, _CEILING),
    ],
    ids=["former-id-paused-last", "former-id-cleared-last"],
)
def test_identity_migration_keeps_the_kept_ids_local_brake_when_a_former_id_wrote_the_newest_fact(
    tmp_path: Path, facts: list[tuple[str, bool, datetime, str | None]], paused: bool, reason: str | None
) -> None:
    """Each id read only its own facts before; after, the brake is the newest fact whichever id wrote it."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        with engine.begin() as conn:
            conn.execute(
                _daemon_liveness.insert(),
                [{"runner_id": "runner-local", "alive_at": _OLD}, {"runner_id": "r-claude", "alive_at": _NEW}],
            )
            conn.execute(
                _local_pause_facts.insert(),
                [{"runner_id": i, "paused": p, "set_at": at, "set_by": "runner", "reason": r} for i, p, at, r in facts],
            )
        _seed_identity_before_the_scope(migration, engine)
        migration.upgrade("head")

        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        assert (store.local_paused(), store.local_pause_reason()) == (paused, reason)
        assert _rows(engine, "SELECT count(*) FROM local_pause_facts") == [(len(facts) + 1,)]
    finally:
        engine.dispose()


def test_identity_migration_downgrades_onto_the_identity_rows_name_and_upgrades_back(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_a_store_spanning_a_rename(engine)
        _seed_identity_before_the_scope(migration, engine)
        migration.upgrade("head")
        store = SqlAlchemyRunnerStore(engine, runner_store_errors(), _RUNNER)
        store.record_runner_identity(RunnerIdentity(_RUNNER, "r-claude", _LATER))
        store.set_hub_paused(paused=True, at=_LATER)

        migration.downgrade(_IDENTITY_PARENT)
        assert "runner_identity" not in sa.inspect(engine).get_table_names()
        assert _rows(engine, "SELECT runner_id, paused FROM hub_control") == [("r-claude", 1)]
        assert _rows(engine, "SELECT runner_id FROM daemon_liveness") == [("r-claude",)]
        assert _rows(engine, "SELECT DISTINCT runner_id FROM local_pause_facts") == [("r-claude",)]
        assert _rows(engine, "SELECT DISTINCT runner_id FROM leases") == [("r-claude",)]
        assert not next(c for c in sa.inspect(engine).get_columns("leases") if c["name"] == "runner_id")["nullable"]

        # Up again the store holds no identity to claim its rows for, so the tightening revision refuses them.
        with pytest.raises(RuntimeError, match="name no runner"):
            migration.upgrade("head")
        assert _rows(engine, "SELECT count(*) FROM runner_identity") == [(0,)]
        assert _rows(engine, "SELECT paused FROM hub_control") == [(1,)]
        with engine.connect() as conn:
            assert conn.execute(sa.select(_liveness_unscoped.c.alive_at)).scalars().all() == [_NEW]
        assert _rows(engine, "SELECT count(*) FROM local_pause_facts") == [(2,)]
    finally:
        engine.dispose()


def test_identity_migration_downgrades_a_never_registered_store_onto_the_pre_change_default_id(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade("head")
        SqlAlchemyRunnerStore(engine, runner_store_errors()).set_hub_paused(paused=True, at=_LATER)
        migration.downgrade(_IDENTITY_PARENT)
        assert _rows(engine, "SELECT runner_id, paused FROM hub_control") == [("runner-local", 1)]
    finally:
        engine.dispose()


@pytest.mark.parametrize("table", _KEYED_TABLES)
def test_frozen_keyed_tables_carry_the_runner_id_until_the_identity_revision(table: str, tmp_path: Path) -> None:
    """``bzh:frozen-revisions``: migrated forward from ``base``, never down from head, so a freeze
    that wrongly absorbed the drop shows."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    engine = create_engine_from_url(url)
    try:
        runner.upgrade(_IDENTITY_PARENT)
        assert "runner_id" in _columns(engine, table)
        assert "runner_identity" not in sa.inspect(engine).get_table_names()
        runner.upgrade(_IDENTITY)
        assert "runner_id" not in _columns(engine, table)
        assert "runner_identity" in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_a_replay_tells_a_lease_minted_before_the_migration_under_the_latest_registration(tmp_path: Path) -> None:
    store = _pre_identity_store(tmp_path)
    identity = RunnerIdentityHolder()
    with pytest.raises(ReplayUnavailable, match="not registered"):
        _replayed(store, identity)

    _register(store, identity, _REGISTERED)
    told = _replayed(store, identity)
    _register(store, identity, _RENAMED)
    retold = _replayed(store, identity)

    assert told and _runners(told) == {(_REGISTERED.runner_id, "r-claude")}
    assert _runners(retold) == {(_REGISTERED.runner_id, "r-claude-2")}
    assert [span.context for span in retold] == [span.context for span in told]


def test_an_upgraded_runner_holds_its_cursor_until_its_first_registration_then_tells_what_closed_meanwhile(
    tmp_path: Path,
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=_REGISTERED.runner_id)
    clock = FixedClock(_CLOSED_AT)
    exporter = InMemoryTraceExporter()

    def sweep(identity: RunnerIdentityHolder) -> LeaseTraceSweep:
        return LeaseTraceSweep(
            leases=store, outbound=store, exporter=exporter, identity=identity, clock=clock, config=_CONFIG
        )

    sweep(RunnerIdentityHolder(_REGISTERED)).sweep()  # the runner before its upgrade opens the cursor
    opened = _cursor_rows(store)
    clock.advance(timedelta(seconds=30))
    closed_lease(store, "lease-01", opened=clock.now() - timedelta(minutes=1), closed=clock.now())
    clock.advance(timedelta(seconds=30))
    identity = RunnerIdentityHolder()
    upgraded = sweep(identity)

    upgraded.sweep()
    assert (exporter.attempts, _cursor_rows(store)) == (0, opened)

    _register(store, identity, _REGISTERED)
    upgraded.sweep()
    told = [span for batch in exporter.batches for span in batch]
    assert told and _runners(told) == {(_REGISTERED.runner_id, "r-claude")}
    assert _cursor_rows(store) > opened
