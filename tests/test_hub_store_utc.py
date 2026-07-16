"""The hub store round-trips its own written instants (issue #28, ``bzh:utc-instants``).

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
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> RunnerRegistryStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return RunnerRegistryStore(create_engine_from_url(db_url))


def test_registration_round_trips_its_own_written_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_registration("r1", workspace_id="ws1", at=_NOW)

    registration = store.get_runner("r1")

    assert registration is not None
    assert registration.registered_at == _NOW
    assert registration.last_seen_at == _NOW
    assert registration.registered_at.tzinfo is not None
    assert registration.last_seen_at.tzinfo is not None


def test_touch_last_seen_round_trips_a_later_instant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_registration("r1", workspace_id="ws1", at=_NOW)
    later = datetime(2026, 7, 16, 12, 5, 0, tzinfo=UTC)

    store.touch_last_seen("r1", at=later)

    registration = store.get_runner("r1")
    assert registration is not None
    assert registration.registered_at == _NOW  # unchanged
    assert registration.last_seen_at == later
