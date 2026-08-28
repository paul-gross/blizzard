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
from blizzard.hub.store.internal.routine_store import RoutineStore

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _store_and_engine(tmp_path: Path) -> tuple[RoutineStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="blizzard", description="", created_at=_NOW))
    return RoutineStore(engine), engine


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
