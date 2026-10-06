"""``ScopeStore`` — the scope repository (component tier).

Exercises ``ensure``/``update``/``record_lifecycle`` — each committing its change row, its
revision compare-and-set, and any lifecycle fact in one transaction — through the read/write
Protocol split (``bzh:repository-split``) on migrated-to-head sqlite. ``ensure``'s
first-write-wins CAS is proven against a pre-seeded row, a losing concurrent second mint."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from structlog.testing import capture_logs

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, Door
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import Scope, ScopeEdit, ScopeSlug
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.schema import config_changes, scope_lifecycle_facts
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[ScopeStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    return ScopeStore(hub_store_connections(engine)), engine


def _store(tmp_path: Path) -> ScopeStore:
    store, _ = _store_and_engine(tmp_path)
    return store


_CTX = ChangeContext(actor="usr_1", door=Door.CLI)


def _mint(slug: str, *, description: str = "", at: datetime = _NOW) -> tuple[Scope, ConfigChange]:
    return Scope.new(ScopeSlug.parse(slug), description, _CTX, at=at)


def _ensure(store: ScopeStore, slug: str, *, description: str = "", at: datetime = _NOW) -> Scope:
    record, change = _mint(slug, description=description, at=at)
    return store.ensure(record, change=change)


def _changes(engine: Engine) -> list[tuple[str, str, int, str]]:
    with engine.connect() as conn:
        rows = conn.execute(select(config_changes).order_by(config_changes.c.id)).all()
    return [(row.record_kind, row.record_key, row.revision, row.op) for row in rows]


def _facts(engine: Engine) -> list[tuple[str, bool, str]]:
    with engine.connect() as conn:
        rows = conn.execute(select(scope_lifecycle_facts).order_by(scope_lifecycle_facts.c.id)).all()
    return [(row.slug, row.retired, row.set_by) for row in rows]


def test_ensure_on_an_unknown_slug_mints_one_row_and_its_create_change(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)

    scope = _ensure(store, "blizzard", description="the blizzard repo")

    assert (scope.slug, scope.description, scope.revision) == ("blizzard", "the blizzard repo", 1)
    assert store.get("blizzard") == scope
    assert _changes(engine) == [("scope", "blizzard", 1, "create")]


def test_ensure_on_an_existing_slug_reads_back_the_existing_row_and_writes_nothing(tmp_path: Path) -> None:
    """The CAS's losing branch: a slug already minted, ``ensure`` called again with
    a different description, leaves the stored description untouched."""
    store, engine = _store_and_engine(tmp_path)
    first = _ensure(store, "blizzard", description="original")

    second = _ensure(store, "blizzard", description="clobber attempt")

    assert second == first
    assert store.get("blizzard").description == "original"  # type: ignore[union-attr]
    assert len(_changes(engine)) == 1


def test_update_writes_the_description_revision_and_change_together(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    scope = _ensure(store, "blizzard", description="original")
    edited, change = scope.edit(ScopeEdit(description="revised"), _CTX, if_match=None, at=_NOW)  # type: ignore[misc]

    store.update(edited, from_revision=1, change=change)

    stored = store.get("blizzard")
    assert (stored.description, stored.revision) == ("revised", 2)  # type: ignore[union-attr]
    assert _changes(engine)[-1] == ("scope", "blizzard", 2, "edit")


def test_update_from_a_moved_revision_conflicts_and_writes_nothing(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    scope = _ensure(store, "blizzard", description="original")
    edited, change = scope.edit(ScopeEdit(description="revised"), _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    store.update(edited, from_revision=1, change=change)

    with pytest.raises(ConfigRevisionConflict) as exc_info:
        store.update(edited, from_revision=1, change=change)

    assert exc_info.value.current == 2
    assert len(_changes(engine)) == 2


def test_get_on_an_unknown_slug_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("ghost") is None


def test_list_all_orders_newest_first_and_marks_retired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = _ensure(store, "old")
    _ensure(store, "new", at=_NOW.replace(hour=13))
    retired, change = old.set_retired(True, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    store.record_lifecycle(retired, retired=True, from_revision=1, at=_NOW, by="paul", change=change)

    assert [(s.slug, s.retired) for s in store.list_all()] == [("new", False), ("old", True)]


def test_a_freshly_minted_scope_is_not_retired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _ensure(store, "blizzard")
    assert store.is_retired("blizzard") is False


def test_retire_then_enable_appends_facts_moves_the_revision_and_logs_each(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    scope = _ensure(store, "blizzard", description="d")

    retired, change = scope.set_retired(True, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    store.record_lifecycle(retired, retired=True, from_revision=1, at=_NOW, by="paul", change=change)
    assert store.get("blizzard") == retired
    enabled, change = retired.set_retired(False, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    store.record_lifecycle(enabled, retired=False, from_revision=2, at=_NOW, by="paul", change=change)

    stored = store.get("blizzard")
    assert (stored.retired, stored.revision, stored.description) == (False, 3, "d")  # type: ignore[union-attr]
    assert _facts(engine) == [("blizzard", True, "paul"), ("blizzard", False, "paul")]
    assert [op for *_, op in _changes(engine)] == ["create", "retire", "enable"]


def test_record_lifecycle_from_a_moved_revision_appends_no_fact(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    scope = _ensure(store, "blizzard")
    retired, change = scope.set_retired(True, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]

    with pytest.raises(ConfigRevisionConflict):
        store.record_lifecycle(retired, retired=True, from_revision=5, at=_NOW, by="paul", change=change)

    assert _facts(engine) == []
    assert len(_changes(engine)) == 1


def test_a_driver_fault_mid_read_raises_the_wrapped_error_and_logs_once(tmp_path: Path) -> None:
    """The schema goes missing out from under an otherwise-healthy engine — a fault
    raised inside the caller's ``with`` block, past connection acquisition, proving the
    seam's wrap site encloses the whole unit of work and not just acquisition."""
    store, engine = _store_and_engine(tmp_path)
    engine.dispose()
    (tmp_path / "hub.db").unlink()

    with capture_logs() as logs, pytest.raises(HubStoreError) as exc_info:
        store.get("blizzard")

    assert exc_info.value.operation == "get"
    assert exc_info.value.detail
    error_logs = [entry for entry in logs if entry["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["operation"] == "get"
