"""``ScopeStore.retired_slugs`` (component tier) — the bulk counterpart to
``is_retired``, mirroring ``tests/test_graph_store_bulk_reads.py``'s coverage of
``GraphStore.retired_graph_ids``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug
from blizzard.hub.store.internal.scope_store import ScopeStore
from tests.support import default_project, hub_store_connections, migrate_to

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


_CTX = ChangeContext(actor="op", door=Door.API)
_SLUGS = ("live", "retired", "re-enabled")


def _mint(store: ScopeStore, project: str, slug: str) -> Scope:
    record, change = Scope.new(ScopeSlug.parse(slug), "", _CTX, at=_T0, project_id=project)
    return store.ensure(record, change=change)


def _flip(store: ScopeStore, scope: Scope, retired: bool) -> Scope:
    decided = scope.set_retired(retired, _CTX, if_match=None, at=_T0)
    if decided is None:
        return scope
    moved, change = decided
    return store.record_lifecycle(moved, retired=retired, from_revision=scope.revision, at=_T0, by="op", change=change)


def _store(tmp_path: Path) -> tuple[ScopeStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    return ScopeStore(hub_store_connections(engine)), engine


def test_retired_slugs_agrees_with_is_retired_across_a_live_a_retired_and_a_re_enabled_scope(
    tmp_path: Path,
) -> None:
    store, engine = _store(tmp_path)
    project = default_project(engine)
    scopes = {slug: _mint(store, project, slug) for slug in _SLUGS}

    _flip(store, _flip(store, scopes["retired"], True), True)  # a repeat decides nothing
    _flip(store, _flip(store, scopes["re-enabled"], True), False)  # newest-fact-wins

    assert store.retired_slugs(project) == {"retired"}
    for slug in _SLUGS:
        assert (slug in store.retired_slugs(project)) == store.is_retired(project, slug)


def test_retired_slugs_of_no_scopes_is_empty(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    project = default_project(engine)
    assert store.retired_slugs(project) == set()
