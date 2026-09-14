"""``ScopeStore.retired_slugs`` (component tier) — the bulk counterpart to
``is_retired``, mirroring ``tests/test_graph_store_bulk_reads.py``'s coverage of
``GraphStore.retired_graph_ids``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.hub.store.internal.scope_store import ScopeStore
from tests.support import hub_store_connections, migrate_to

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ScopeStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    return ScopeStore(hub_store_connections(engine)), engine


def test_retired_slugs_agrees_with_is_retired_across_a_live_a_retired_and_a_re_enabled_scope(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    store.ensure("live", description="", at=_T0)
    store.ensure("retired", description="", at=_T0)
    store.ensure("re-enabled", description="", at=_T0)

    store.record_lifecycle("retired", retired=True, at=_T0, by="op")
    store.record_lifecycle("re-enabled", retired=True, at=_T0, by="op")
    store.record_lifecycle("re-enabled", retired=False, at=_T0.replace(hour=1), by="op")  # newest-fact-wins

    assert store.retired_slugs() == {"retired"}
    for slug in ("live", "retired", "re-enabled"):
        assert (slug in store.retired_slugs()) == store.is_retired(slug)


def test_retired_slugs_of_no_scopes_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert store.retired_slugs() == set()
