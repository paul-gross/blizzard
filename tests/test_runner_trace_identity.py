"""Which runner a lease's spans name (component tier) — the id and name of the runner's latest
registration, read from its store's identity row as each lease is told: a lease minted before the
store kept that row is told under the hub-minted id, a rename by restart shows from the next telling,
and nothing is told before the first registration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.runner.hub.identity import RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.tracing.attributes import RUNNER_ID, RUNNER_NAME
from blizzard.runner.tracing.replay import LeaseTraceReplay, ReplayUnavailable
from blizzard.runner.tracing.sweep import LeaseTraceSweep
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_store_errors
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter

pytestmark = pytest.mark.component

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
    the real migration to head — which keeps the lease and leaves the identity row empty."""
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
    migrations.upgrade("head")
    return SqlAlchemyRunnerStore(create_engine_from_url(url), runner_store_errors())


def _replayed(store: SqlAlchemyRunnerStore, identity: RunnerIdentityHolder) -> list[FinishedSpan]:
    exporter = InMemoryTraceExporter()
    replay = LeaseTraceReplay(leases=store, exporter=exporter, identity=identity, config=_CONFIG)
    replay.replay(_CLOSED_AT - timedelta(minutes=10), _CLOSED_AT + timedelta(minutes=10), dry_run=False)
    return [span for batch in exporter.batches for span in batch]


def _runners(spans: list[FinishedSpan]) -> set[tuple[object, object]]:
    return {(span.attributes.get(RUNNER_ID), span.attributes.get(RUNNER_NAME)) for span in spans}


def _register(store: SqlAlchemyRunnerStore, identity: RunnerIdentityHolder, registered: RunnerIdentity) -> None:
    """What a successful registration leaves: the row first, then the process's holder."""
    store.record_runner_identity(registered)
    identity.hold(registered)


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


def _cursor_rows(store: SqlAlchemyRunnerStore) -> int:
    with store._engine.connect() as conn:
        return conn.exec_driver_sql("SELECT count(*) FROM trace_cursor").scalar_one()


def test_an_upgraded_runner_holds_its_cursor_until_its_first_registration_then_tells_what_closed_meanwhile(
    tmp_path: Path,
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
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
