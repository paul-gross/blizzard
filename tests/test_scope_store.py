"""``ScopeStore`` — the scope repository (blizzard#389, component tier).

Exercises ``ensure``/``edit_description``/``record_lifecycle`` through the read/write
Protocol split (``bzh:repository-split``), migrated-to-head sqlite-on-disk — the
``tests/test_work_item_store.py`` shape. ``ensure``'s first-write-wins CAS (D5) is
proven directly against a pre-seeded row, mirroring a losing concurrent second mint."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.internal.scope_store import ScopeStore

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[ScopeStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    return ScopeStore(engine), engine


def _store(tmp_path: Path) -> ScopeStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def test_ensure_on_an_unknown_slug_mints_one_row(tmp_path: Path) -> None:
    store = _store(tmp_path)

    scope = store.ensure("blizzard", description="the blizzard repo", at=_NOW)

    assert scope.slug == "blizzard"
    assert scope.description == "the blizzard repo"
    assert store.get("blizzard") == scope


def test_ensure_on_an_existing_slug_reads_back_the_existing_row_unchanged(tmp_path: Path) -> None:
    """The CAS's losing branch (D5): a slug already minted, ``ensure`` called again with
    a different description, leaves the stored description untouched (D4)."""
    store = _store(tmp_path)
    first = store.ensure("blizzard", description="original", at=_NOW)

    second = store.ensure("blizzard", description="clobber attempt", at=_NOW)

    assert second == first
    assert store.get("blizzard").description == "original"  # type: ignore[union-attr]


def test_edit_description_changes_it_in_place(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure("blizzard", description="original", at=_NOW)

    edited = store.edit_description("blizzard", description="revised")

    assert edited.description == "revised"
    assert store.get("blizzard").description == "revised"  # type: ignore[union-attr]


def test_get_on_an_unknown_slug_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("ghost") is None


def test_list_all_orders_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure("old", description="", at=_NOW)
    store.ensure("new", description="", at=_NOW.replace(hour=13))

    slugs = [s.slug for s in store.list_all()]

    assert slugs == ["new", "old"]


def test_a_freshly_minted_scope_is_not_retired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure("blizzard", description="", at=_NOW)
    assert store.is_retired("blizzard") is False


def test_retire_then_enable_derives_not_retired_and_leaves_the_row_untouched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = store.ensure("blizzard", description="d", at=_NOW)

    store.record_lifecycle("blizzard", retired=True, at=_NOW, by="paul")
    assert store.is_retired("blizzard") is True

    store.record_lifecycle("blizzard", retired=False, at=_NOW, by="paul")
    assert store.is_retired("blizzard") is False
    assert store.get("blizzard") == before


def test_a_second_retire_is_a_harmless_no_op(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.ensure("blizzard", description="", at=_NOW)

    store.record_lifecycle("blizzard", retired=True, at=_NOW, by="paul")
    store.record_lifecycle("blizzard", retired=True, at=_NOW, by="paul")

    assert store.is_retired("blizzard") is True
