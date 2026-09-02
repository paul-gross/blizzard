"""``build_chunk_stores``/``build_services`` expose the new chunk-dependencies seam on
both the write (``ChunkStores``) and read-only (``ChunkReadStores``) bundles (issue #456,
component tier) — the read bundle typed to the read Protocol so
``bzh:controller-read-only`` holds at type-check time; this asserts the runtime half:
both bundles carry the field, and it is the same underlying adapter instance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.store.internal.chunk_dependencies_store import ChunkDependenciesStore
from tests.support import build_hub, chunk_stores, migrate_to

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, tzinfo=UTC)


def test_the_write_bundle_exposes_the_dependencies_seam(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")

    stores = chunk_stores(engine, FixedClock(instant=_NOW))

    assert isinstance(stores.dependencies, ChunkDependenciesStore)


def test_the_read_bundle_exposes_the_same_dependencies_adapter(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    dependencies: IReadChunkDependenciesRepository = hub.services.chunks.dependencies

    # Identity, not just isinstance — the very same adapter instance `DependencyService`
    # holds for its own writes, not a second, disconnected object.
    assert dependencies is hub.services.dependencies._dependencies
    assert isinstance(dependencies, ChunkDependenciesStore)
    assert dependencies.list_standing_edges() == []
