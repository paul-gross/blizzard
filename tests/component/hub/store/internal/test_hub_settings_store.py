"""Hub-wide settings against a migrated store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Entry
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.store.internal.hub_settings_store import HubSettingsStore
from blizzard.hub.store.schema import hub_setting_changes
from tests.support.scaffolding import count_queries, hub_level_connections, migrate_to


def test_write_audit_conflict_and_reader_cache(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), lambda _: {})
    reader = HubSettingsReader(store)
    assert reader.snapshot().revision == 0
    assert count_queries(engine, reader.snapshot) == 1
    change = Change("edit", sets={"follow_latest": True})
    store.apply(change, if_match=0, door="api", actor="operator")
    assert reader.snapshot().entries["follow_latest"] == Entry(True)
    assert count_queries(engine, reader.snapshot) == 1
    assert not store.apply(change, if_match=1, door="api", actor="operator")
    with pytest.raises(SettingsRevisionConflict):
        store.apply(change, if_match=0, door="api", actor="operator")
    store.apply(Change("edit", unsets=frozenset({"follow_latest"})), if_match=1, door="api", actor="operator")
    assert reader.snapshot().entries["follow_latest"] == Entry(None, at_default=True)
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(hub_setting_changes)).scalar_one() == 2


def test_invalid_write_leaves_no_changes(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(
        hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), lambda _: {"x": "invalid"}
    )
    with pytest.raises(SettingsInvalid):
        store.apply(Change("edit", sets={"x": 1}), if_match=0, door="api", actor="operator")
    assert store.snapshot().revision == 0
    assert not store.snapshot().entries


def test_migration_round_trip_keeps_tenant_data(tmp_path: Path) -> None:
    from blizzard.hub.store.schema import tenants
    from tests.support.scaffolding import add_tenant

    runner, engine = migrate_to(tmp_path, "20261111_1000_chunk_repository_targets")
    tenant = add_tenant(engine, "retained")
    runner.upgrade("head")
    runner.downgrade("20261111_1000_chunk_repository_targets")
    runner.upgrade("head")
    with engine.connect() as conn:
        assert conn.execute(sa.select(tenants.c.name).where(tenants.c.tenant_id == tenant)).scalar_one() == "retained"
        assert conn.execute(sa.select(sa.func.count()).select_from(hub_setting_changes)).scalar_one() == 0


def test_failure_during_audit_rolls_back_revision_and_entries(tmp_path: Path) -> None:
    class FailedClock:
        def now(self) -> datetime:
            raise RuntimeError("clock failed")

    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(hub_level_connections(engine), FailedClock(), lambda _: {})
    with pytest.raises(RuntimeError, match="clock failed"):
        store.apply(Change("edit", sets={"x": 1}), if_match=0, door="api", actor="operator")
    assert store.snapshot().revision == 0
    assert not store.snapshot().entries


def test_seed_only_absent_preserves_tombstone_and_atomic_values(tmp_path: Path) -> None:
    from blizzard.foundation.flags import FlagRegistry
    from blizzard.hub.config_keys import HUB_REGISTRY
    from blizzard.hub.settings.footing import HubSettingsFooting, setting_keys

    _, engine = migrate_to(tmp_path, "head")
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    store = HubSettingsStore(
        hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), footing.validate
    )
    file = {"follow_latest": True, "trusted_proxies": ["127.0.0.1"]}
    store.seed(setting_keys(HUB_REGISTRY), footing.file_view(file))
    first = store.snapshot()
    assert first.entries["follow_latest"] == Entry(True)
    assert first.entries["trusted_proxies"] == Entry(("127.0.0.1",))
    assert first.revision == 2
    store.apply(Change("edit", unsets=frozenset({"follow_latest"})), if_match=2, door="api", actor="operator")
    store.seed(
        setting_keys(HUB_REGISTRY), footing.file_view({"follow_latest": True, "public_url": "https://example.com"})
    )
    restarted = store.snapshot()
    assert restarted.entries["follow_latest"] == Entry(None, True)
    assert footing.resolve(restarted.entries).follow_latest is False
    assert restarted.entries["public_url"] == Entry("https://example.com")
    assert restarted.revision == 4


def test_two_writers_one_wins(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), lambda _: {})
    barrier = Barrier(2)

    def attempt(value: int) -> str:
        barrier.wait()
        try:
            store.apply(Change("edit", sets={"x": value}), if_match=0, door="api", actor="operator")
        except SettingsRevisionConflict:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, (1, 2))) == ["committed", "conflict"]
    assert store.snapshot().revision == 1


def test_cache_miss_uses_snapshot_revision_not_earlier_probe(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), lambda _: {})

    class Interleaved:
        def revision(self) -> int:
            revision = store.revision()
            store.apply(Change("edit", sets={"x": 1}), if_match=revision, door="api", actor="operator")
            return revision

        def snapshot(self):
            return store.snapshot()

    result = HubSettingsReader(Interleaved()).snapshot()
    assert result.revision == 1
    assert result.entries["x"] == Entry(1)


def test_nested_snapshot_values_cannot_be_mutated(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    store = HubSettingsStore(hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), lambda _: {})
    store.apply(Change("edit", sets={"x": [{"y": [1]}]}), if_match=0, door="api", actor="operator")
    snapshot = store.snapshot()
    value = snapshot.entries["x"].value
    assert isinstance(value, tuple)
    assert value[0]["y"] == (1,)
    with pytest.raises(TypeError):
        value[0]["y"] = ()


def test_complete_config_validation_refuses_bad_values_and_unknown_keys(tmp_path: Path) -> None:
    from blizzard.foundation.flags import FlagRegistry
    from blizzard.hub.config_keys import HUB_REGISTRY
    from blizzard.hub.settings.footing import HubSettingsFooting

    _, engine = migrate_to(tmp_path, "head")
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), True, HUB_REGISTRY, {})
    store = HubSettingsStore(
        hub_level_connections(engine), FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), footing.validate
    )
    for sets in (
        {"route_token_mode": "wrong"},
        {"auth.login_throttle.capacity": 0},
        {"action_pass_seconds": 0},
        {"annotation_interval_seconds": -1},
        {"not_a_setting": True},
    ):
        with pytest.raises(SettingsInvalid):
            store.apply(Change("edit", sets=sets), if_match=0, door="api", actor="operator")
    assert store.revision() == 0
