"""Settings writes are scoped, revision-conditioned and atomic."""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier, Event, current_thread, main_thread

import pytest
from sqlalchemy import event

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Entry
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from blizzard.runner.store.scoped import RunnerStoreConnections
from tests.support.runner_fakes import make_store, runner_store_errors
from tests.support.scaffolding import count_queries, count_rows_read


def _valid(entries: Mapping[str, Entry]) -> Mapping[str, str]:
    return {}


def test_writes_validate_and_keep_revision_and_identity_atomic(tmp_path: Path) -> None:
    base = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    identity = RunnerIdentityStore(base.connections)
    identity.record_runner_identity(RunnerIdentity(base.connections.bound_runner_id(), "one", now))
    settings = RunnerSettingsStore(base.connections, FixedClock(now))

    settings.write(Change("edit", sets={"agents": 2}), if_match=0, validate=_valid, door="cli", actor="test")
    assert settings.snapshot().entries == {"agents": Entry(2)}
    with pytest.raises(SettingsRevisionConflict):
        settings.write(Change("edit", sets={"agents": 3}), if_match=0, validate=_valid, door="cli", actor="test")
    with pytest.raises(SettingsInvalid):
        settings.write(
            Change("edit", sets={"agents": 3}),
            if_match=1,
            validate=lambda _: {"agents": "bad"},
            door="cli",
            actor="test",
        )
    assert settings.snapshot().revision == 1
    assert not settings.write(Change("edit", sets={"agents": 2}), if_match=1, validate=_valid, door="cli", actor="test")
    assert settings.snapshot().revision == 1
    settings.write(Change("edit", unsets=frozenset({"agents"})), if_match=1, validate=_valid, door="cli", actor="test")
    assert settings.snapshot().entries == {"agents": Entry(None, at_default=True)}
    assert identity.forget_runner_identity() is False
    sibling = RunnerSettingsStore(
        RunnerStoreConnections(base.engine, runner_store_errors(), "sibling"), FixedClock(now)
    )
    assert sibling.snapshot().revision == 0
    assert not sibling.snapshot().entries


def test_seed_preserves_existing_entries_and_unsets(tmp_path: Path) -> None:
    from blizzard.foundation.config_keys import ConfigView

    base = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(base.connections.bound_runner_id(), "one", now)
    )
    settings = RunnerSettingsStore(base.connections, FixedClock(now))
    settings.seed(
        ConfigView.unchecked({"agents": 3, "worker": {"path": ["a", "b"]}}),
        ["agents", "worker.path", "missing"],
        validate=_valid,
    )
    assert settings.snapshot().entries == {"agents": Entry(3), "worker.path": Entry(["a", "b"])}
    settings.write(Change("edit", unsets=frozenset({"agents"})), if_match=2, validate=_valid, door="cli", actor="test")
    settings.seed(
        ConfigView.unchecked({"agents": 8, "worker": {"path": ["c"]}}), ["agents", "worker.path"], validate=_valid
    )
    assert settings.snapshot().entries == {"agents": Entry(None, True), "worker.path": Entry(["a", "b"])}
    assert settings.snapshot().revision == 3


def test_history_and_provenance_are_bounded(tmp_path: Path) -> None:
    base = make_store(f"sqlite:///{tmp_path / 'history.db'}")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(base.connections.bound_runner_id(), "one", now)
    )
    settings = RunnerSettingsStore(base.connections, FixedClock(now))

    def grow(start: int, end: int) -> None:
        for revision in range(start, end):
            settings.write(
                Change("edit", sets={"agents": revision}), if_match=revision, validate=_valid, door="cli", actor="test"
            )

    grow(0, 5)
    reads = (
        count_queries(base.engine, lambda: settings.changes(limit=3)),
        count_rows_read(base.engine, lambda: settings.changes(limit=3)),
        count_rows_read(base.engine, settings.provenance),
    )
    grow(5, 25)
    assert (
        reads
        == (
            count_queries(base.engine, lambda: settings.changes(limit=3)),
            count_rows_read(base.engine, lambda: settings.changes(limit=3)),
            count_rows_read(base.engine, settings.provenance),
        )
        == (2, 3, 1)  # SQLite BEGIN plus SELECT.
    )
    page = settings.changes(limit=3)
    assert [c.revision for c in page] == [25, 24, 23]
    assert [c.revision for c in settings.changes(limit=3, before=page[-1].id)] == [22, 21, 20]
    assert settings.provenance()["agents"].revision == 25
    assert settings.provenance()["agents"].diff[0].new == Entry(24)
    sibling = RunnerSettingsStore(
        RunnerStoreConnections(base.engine, runner_store_errors(), "sibling"), FixedClock(now)
    )
    assert sibling.changes(limit=3) == ()
    assert sibling.provenance() == {}


def test_competing_writers_and_coherent_snapshot(tmp_path: Path) -> None:
    base = make_store(f"sqlite:///{tmp_path / 'concurrent.db'}")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(base.connections.bound_runner_id(), "one", now)
    )
    settings = RunnerSettingsStore(base.connections, FixedClock(now))
    barrier = Barrier(2)

    def writer(value: int) -> bool:
        barrier.wait(timeout=5)
        try:
            settings.write(
                Change("edit", sets={"agents": value}), if_match=0, validate=_valid, door="cli", actor="test"
            )
        except SettingsRevisionConflict:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(writer, (1, 2))) == [False, True]
    old = settings.snapshot()
    read_revision, proceed = Event(), Event()

    def pause(
        conn: object, cursor: object, statement: str, parameters: object, context: object, executemany: object
    ) -> None:
        if (
            current_thread() is not main_thread()
            and statement.lstrip().upper().startswith("SELECT")
            and "FROM runner_settings" in statement
        ):
            read_revision.set()
            assert proceed.wait(timeout=5)

    event.listen(base.engine, "after_cursor_execute", pause)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(settings.snapshot)
            assert read_revision.wait(timeout=5)
            settings.write(Change("edit", sets={"agents": 9}), if_match=1, validate=_valid, door="cli", actor="test")
            proceed.set()
            assert future.result(timeout=5) == old
    finally:
        proceed.set()
        if event.contains(base.engine, "after_cursor_execute", pause):
            event.remove(base.engine, "after_cursor_execute", pause)
    assert settings.snapshot().revision == 2
    assert settings.snapshot().entries == {"agents": Entry(9)}


def _identified(tmp_path: Path, clock: object) -> tuple[RunnerSettingsStore, RunnerSettingsStore]:
    base = make_store(f"sqlite:///{tmp_path / 'rollback.db'}")
    now = datetime(2026, 11, 1, tzinfo=UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(base.connections.bound_runner_id(), "one", now)
    )
    return RunnerSettingsStore(base.connections, clock), RunnerSettingsStore(base.connections, FixedClock(now))  # type: ignore[arg-type]


def test_a_failed_audit_insert_rolls_back_revision_and_entries(tmp_path: Path) -> None:
    class AuditFails:
        def __init__(self) -> None:
            self.calls = 0

        def now(self) -> datetime:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("audit failed")
            return datetime(2026, 11, 1, tzinfo=UTC)

    failing, healthy = _identified(tmp_path, AuditFails())
    with pytest.raises(RuntimeError, match="audit failed"):
        failing.write(Change("edit", sets={"agents": 2}), if_match=0, validate=_valid, door="cli", actor="test")
    assert healthy.snapshot().revision == 0
    assert not healthy.snapshot().entries
    assert healthy.changes(limit=3) == ()


def test_a_moving_write_records_its_provenance(tmp_path: Path) -> None:
    _, settings = _identified(tmp_path, FixedClock(datetime(2026, 11, 1, tzinfo=UTC)))
    settings.write(
        Change("edit", sets={"agents": 2}), if_match=0, validate=_valid, door="api", actor="me", apply_id="apply_1"
    )
    (change,) = settings.changes(limit=3)
    assert (change.door, change.actor, change.apply_id, change.revision) == ("api", "me", "apply_1", 1)


def test_two_concurrent_seeds_yield_one_revision_per_key(tmp_path: Path) -> None:
    from blizzard.foundation.config_keys import ConfigView

    _, settings = _identified(tmp_path, FixedClock(datetime(2026, 11, 1, tzinfo=UTC)))
    file = ConfigView.unchecked({"agents": 3, "worker": {"path": ["a"]}})
    barrier = Barrier(2)

    def seed(_: int) -> None:
        barrier.wait(timeout=5)
        settings.seed(file, ["agents", "worker.path"], validate=_valid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(seed, (1, 2)))
    assert settings.snapshot().entries == {"agents": Entry(3), "worker.path": Entry(["a"])}
    assert settings.snapshot().revision == 2
    assert [c.revision for c in settings.changes(limit=5)] == [2, 1]
