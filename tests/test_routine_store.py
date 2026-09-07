"""``RoutineStore`` — the routine repository (blizzard#389, component tier).

Migrated-to-head sqlite-on-disk — the ``tests/test_work_item_store.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.routines import Routine
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[RoutineStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    return RoutineStore(hub_store_connections(engine)), engine


def _store(tmp_path: Path) -> RoutineStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def _routine(**overrides: object) -> Routine:
    fields: dict[str, object] = {
        "routine_id": "rtn_1",
        "name": "nightly",
        "graph_name": "alpha",
        "default_scope_slug": "blizzard",
        "created_at": _NOW,
        "default_model": ["blizzard:advanced"],
        "default_effort": "high",
    }
    fields.update(overrides)
    return Routine(**fields)  # type: ignore[arg-type]


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    routine = _routine()

    store.create(routine)

    assert store.get("rtn_1") == routine


def test_create_with_an_empty_model_preference_reads_back_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_routine(default_model=[]))

    assert store.get("rtn_1").default_model == []  # type: ignore[union-attr]


def test_get_by_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_routine())

    assert store.get_by_name("nightly") == store.get("rtn_1")
    assert store.get_by_name("ghost") is None


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("rtn_ghost") is None


def test_list_all_orders_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_routine(routine_id="rtn_old", name="old", created_at=_NOW))
    store.create(_routine(routine_id="rtn_new", name="new", created_at=_NOW.replace(hour=13)))

    ids = [r.routine_id for r in store.list_all()]

    assert ids == ["rtn_new", "rtn_old"]


def test_edit_changes_everything_but_name_and_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(_routine())

    edited = store.edit(
        "rtn_1", graph_name="beta", default_scope_slug="blizzard", default_model=["basic"], default_effort="low"
    )

    assert edited.routine_id == "rtn_1"
    assert edited.name == "nightly"
    assert edited.graph_name == "beta"
    assert edited.default_model == ["basic"]
    assert edited.default_effort == "low"
    assert store.get("rtn_1") == edited


# --- RoutineScopeStore (the routine_scopes join, blizzard#488) --------------------


def _routine_scope_store(tmp_path: Path) -> RoutineScopeStore:
    routine_store, engine = _store_and_engine(tmp_path)
    routine_store.create(_routine())
    with engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="other", description="", created_at=_NOW))
    return RoutineScopeStore(hub_store_connections(engine))


def test_link_then_list_scopes_round_trips(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    store.link("rtn_1", "blizzard")
    store.link("rtn_1", "other")

    assert store.list_scopes("rtn_1") == ["blizzard", "other"]


def test_link_is_idempotent(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    store.link("rtn_1", "blizzard")
    store.link("rtn_1", "blizzard")

    assert store.list_scopes("rtn_1") == ["blizzard"]


def test_unlink_removes_the_link(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)
    store.link("rtn_1", "blizzard")

    store.unlink("rtn_1", "blizzard")

    assert store.list_scopes("rtn_1") == []


def test_unlink_is_idempotent_when_not_linked(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    store.unlink("rtn_1", "blizzard")  # no-op — never linked

    assert store.list_scopes("rtn_1") == []


def test_list_scopes_for_an_unlinked_routine_is_empty(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    assert store.list_scopes("rtn_ghost") == []


def test_list_routines_returns_the_linked_routine_ids(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    store.link("rtn_1", "blizzard")

    assert store.list_routines("blizzard") == ["rtn_1"]


def test_list_routines_orders_by_routine_id(tmp_path: Path) -> None:
    routine_store, engine = _store_and_engine(tmp_path)
    routine_store.create(_routine(routine_id="rtn_b", name="b"))
    routine_store.create(_routine(routine_id="rtn_a", name="a"))
    store = RoutineScopeStore(hub_store_connections(engine))

    store.link("rtn_b", "blizzard")
    store.link("rtn_a", "blizzard")

    assert store.list_routines("blizzard") == ["rtn_a", "rtn_b"]


def test_list_routines_for_an_unlinked_scope_is_empty(tmp_path: Path) -> None:
    store = _routine_scope_store(tmp_path)

    assert store.list_routines("other") == []


def test_unlinking_a_pair_leaves_its_findings_readable(tmp_path: Path) -> None:
    """AC6 (blizzard#488): a finding recorded under a `(routine, scope)` pair stays
    readable through `FindingStore.list_for` after that pair is unlinked — no finding
    read joins through `routine_scopes` (D1, D2)."""
    routine_store, engine = _store_and_engine(tmp_path)
    routine_store.create(_routine())
    scope_store = RoutineScopeStore(hub_store_connections(engine))
    finding_store = FindingStore(hub_store_connections(engine))
    scope_store.link("rtn_1", "blizzard")
    finding_store.add(
        "fnd_1",
        routine_name="nightly",
        scope_slug="blizzard",
        class_="style",
        locus="src/example.py:1",
        summary="an example finding",
        introduced=None,
        at=_NOW,
    )

    scope_store.unlink("rtn_1", "blizzard")

    assert scope_store.list_scopes("rtn_1") == []
    assert [f.finding_id for f in finding_store.list_for("nightly", "blizzard")] == ["fnd_1"]
