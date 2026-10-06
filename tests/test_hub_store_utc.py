"""The hub store round-trips its own written instants (``bzh:utc-instants``).

``record_lease(created_at=_NOW)`` (well, its fleet-registry sibling here) must read
back ``== _NOW`` and UTC-aware — impossible before the schema's ``DateTime`` columns
were retyped ``UtcDateTime``, since sqlite drops ``tzinfo`` on write.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerCapability
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> RunnerRegistryStore:
    """A migrated store holding the added runner ``r1``, ready for its registrations."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    store = RunnerRegistryStore(hub_store_connections(create_engine_from_url(db_url)))
    store.add(RunnerAddition("r1", "r1", "hash-r1", at=_NOW, by="test"))
    return store


def test_registration_round_trips_its_own_written_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_registration("r1", workspace_id="ws1", env_capacity=None, at=_NOW)

    registration = store.get_runner("r1")

    assert registration is not None
    assert registration.registered_at == _NOW
    assert registration.last_seen_at == _NOW
    assert registration.registered_at is not None and registration.registered_at.tzinfo is not None
    assert registration.last_seen_at is not None and registration.last_seen_at.tzinfo is not None


def test_touch_last_seen_round_trips_a_later_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_registration("r1", workspace_id="ws1", env_capacity=None, at=_NOW)
    later = datetime(2026, 7, 16, 12, 5, 0, tzinfo=UTC)

    store.touch_last_seen("r1", at=later)

    registration = store.get_runner("r1")
    assert registration is not None
    assert registration.registered_at == _NOW  # unchanged
    assert registration.last_seen_at == later


def test_registration_round_trips_its_capability_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path)
    capabilities = (
        RunnerCapability(harness_id="claude_code", version="1.2.3", tiers=("blizzard:frontier",), default=True),
    )

    store.record_registration("r1", workspace_id="ws1", env_capacity=None, capabilities=capabilities, at=_NOW)

    registration = store.get_runner("r1")
    assert registration is not None
    assert registration.capabilities == capabilities


def test_registration_without_capabilities_leaves_it_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_registration("r1", workspace_id="ws1", env_capacity=None, at=_NOW)

    registration = store.get_runner("r1")
    assert registration is not None
    assert registration.capabilities == ()


def test_reregistration_replaces_the_capability_snapshot_whole(tmp_path: Path) -> None:
    # A dropped binding/tier leaves no trace, and omitting the field on re-registration
    # clears whatever was there before — a synchronous overwrite, not a merge.
    store = _store(tmp_path)
    store.record_registration(
        "r1",
        workspace_id="ws1",
        env_capacity=None,
        capabilities=(
            RunnerCapability(
                harness_id="claude_code", version="1.0.0", tiers=("blizzard:frontier", "blizzard:basic"), default=True
            ),
        ),
        at=_NOW,
    )

    store.record_registration("r1", workspace_id="ws1", env_capacity=None, at=_NOW)

    registration = store.get_runner("r1")
    assert registration is not None
    assert registration.capabilities == ()
