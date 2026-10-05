"""``RoutineStore`` — the routine repository (component tier).

Each write commits the row, its revision compare-and-set, any lifecycle fact or
``routine_scopes`` link, a minted default scope, and every change row in one transaction.
Migrated-to-head sqlite-on-disk — the ``tests/test_work_item_store.py`` shape."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import Routine, RoutineNameTakenError
from blizzard.hub.domain.garden.scopes import Scope, ScopeMint, ScopeSlug
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.scope_store import ScopeStore
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


_CTX = ChangeContext(actor="usr_1", door=Door.CLI)


def _change(routine: Routine, op: ChangeOp = ChangeOp.CREATE) -> ConfigChange:
    return ConfigChange.of(_CTX, RecordKind.ROUTINE, routine.name, routine.revision, op, (), _NOW)


def _create(store: RoutineStore, routine: Routine, *, scope_mint: ScopeMint | None = None) -> Routine:
    return store.create(routine, change=_change(routine), scope_mint=scope_mint)


def _changes(engine: Engine) -> list[tuple[str, str, int, str]]:
    with engine.connect() as conn:
        rows = conn.execute(select(s.config_changes).order_by(s.config_changes.c.id)).all()
    return [(row.record_kind, row.record_key, row.revision, row.op) for row in rows]


def _retire(store: RoutineStore, routine: Routine, retired: bool = True) -> Routine:
    moved, change = routine.set_retired(retired, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    return store.record_lifecycle(
        moved, retired=retired, from_revision=routine.revision, at=_NOW, by="paul", change=change
    )


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    routine = _routine()

    _create(store, routine)

    assert store.get("rtn_1") == routine


def test_create_links_the_default_and_commits_its_change(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)

    _create(store, _routine())

    assert RoutineScopeStore(hub_store_connections(engine)).list_scopes("rtn_1") == ["blizzard"]
    assert _changes(engine) == [("routine", "nightly", 1, "create")]


def test_create_with_a_scope_mint_commits_the_scope_and_both_changes(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    mint = Scope.new(ScopeSlug.parse("fresh"), "", _CTX, at=_NOW)

    _create(store, _routine(default_scope_slug="fresh"), scope_mint=mint)

    assert ScopeStore(hub_store_connections(engine)).get("fresh") is not None
    assert _changes(engine) == [("scope", "fresh", 1, "create"), ("routine", "nightly", 1, "create")]


def test_a_refused_create_leaves_its_scope_mint_unwritten(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    _create(store, _routine())
    mint = Scope.new(ScopeSlug.parse("fresh"), "", _CTX, at=_NOW)

    with pytest.raises(RoutineNameTakenError):
        _create(store, _routine(routine_id="rtn_2", default_scope_slug="fresh"), scope_mint=mint)

    assert ScopeStore(hub_store_connections(engine)).get("fresh") is None
    assert len(_changes(engine)) == 1


def test_create_with_an_empty_model_preference_reads_back_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine(default_model=[]))

    assert store.get("rtn_1").default_model == []  # type: ignore[union-attr]


def test_create_with_a_harnesses_preference_reads_back(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine(default_harnesses=["claude_code", "codex"]))

    assert store.get("rtn_1").default_harnesses == ["claude_code", "codex"]  # type: ignore[union-attr]


def test_create_with_no_harnesses_preference_reads_back_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine(default_harnesses=[]))

    assert store.get("rtn_1").default_harnesses == []  # type: ignore[union-attr]


def test_get_by_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine())

    assert store.get_by_name("nightly") == store.get("rtn_1")
    assert store.get_by_name("ghost") is None


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("rtn_ghost") is None


def test_list_all_orders_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine(routine_id="rtn_old", name="old", created_at=_NOW))
    _create(store, _routine(routine_id="rtn_new", name="new", created_at=_NOW.replace(hour=13)))

    ids = [r.routine_id for r in store.list_all()]

    assert ids == ["rtn_new", "rtn_old"]


def test_update_changes_everything_but_name_and_id_and_moves_the_revision(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    routine = _create(store, _routine())
    edited = replace(
        routine,
        graph_name="beta",
        default_model=["basic"],
        default_effort="low",
        default_harnesses=["claude_code"],
        revision=2,
    )

    store.update(edited, from_revision=1, change=_change(edited, ChangeOp.EDIT), scope_mint=None)

    assert store.get("rtn_1") == edited
    assert _changes(engine)[-1] == ("routine", "nightly", 2, "edit")


def test_update_to_a_minted_default_links_it_and_commits_its_create(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    routine = _create(store, _routine())
    edited = replace(routine, default_scope_slug="fresh", revision=2)
    mint = Scope.new(ScopeSlug.parse("fresh"), "", _CTX, at=_NOW)

    store.update(edited, from_revision=1, change=_change(edited, ChangeOp.EDIT), scope_mint=mint)

    assert RoutineScopeStore(hub_store_connections(engine)).list_scopes("rtn_1") == ["blizzard", "fresh"]
    assert _changes(engine)[1:] == [("scope", "fresh", 1, "create"), ("routine", "nightly", 2, "edit")]


def test_update_from_a_moved_revision_conflicts_and_writes_nothing(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    routine = _create(store, _routine())
    edited = replace(routine, graph_name="beta", default_scope_slug="fresh", revision=2)
    mint = Scope.new(ScopeSlug.parse("fresh"), "", _CTX, at=_NOW)

    with pytest.raises(ConfigRevisionConflict) as exc_info:
        store.update(edited, from_revision=4, change=_change(edited, ChangeOp.EDIT), scope_mint=mint)

    assert exc_info.value.current == 1
    assert store.get("rtn_1") == routine
    assert ScopeStore(hub_store_connections(engine)).get("fresh") is None
    assert len(_changes(engine)) == 1


# --- Lifecycle (retire/enable brake) — the ScopeStore shape ----------


def test_a_freshly_minted_routine_is_not_retired(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine())
    assert store.is_retired("rtn_1") is False


def test_retire_then_enable_moves_the_revision_and_logs_each(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    routine = _create(store, _routine())

    retired = _retire(store, routine)
    assert store.get("rtn_1") == retired
    assert store.is_retired("rtn_1") is True

    enabled = _retire(store, retired, retired=False)
    assert store.get("rtn_1") == enabled
    assert (enabled.retired, enabled.revision) == (False, 3)
    assert [op for *_, op in _changes(engine)] == ["create", "retire", "enable"]


def test_record_lifecycle_from_a_moved_revision_appends_no_fact(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    routine = _create(store, _routine())
    moved, change = routine.set_retired(True, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]

    with pytest.raises(ConfigRevisionConflict):
        store.record_lifecycle(moved, retired=True, from_revision=9, at=_NOW, by="paul", change=change)

    assert store.is_retired("rtn_1") is False
    assert len(_changes(engine)) == 1


def test_retired_ids_and_list_all_reflect_only_the_newest_fact_per_routine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = _create(store, _routine(routine_id="rtn_a", name="a"))
    b = _create(store, _routine(routine_id="rtn_b", name="b"))

    _retire(store, a)
    _retire(store, _retire(store, b), retired=False)

    assert store.retired_ids() == {"rtn_a"}
    assert {r.routine_id: r.retired for r in store.list_all()} == {"rtn_a": True, "rtn_b": False}


# --- RoutineScopeStore (the routine_scopes join) --------------------


def _routine_scope_store(tmp_path: Path) -> tuple[RoutineScopeStore, Routine, Engine]:
    routine_store, engine = _store_and_engine(tmp_path)
    routine = _create(routine_store, _routine())
    with engine.begin() as conn:
        conn.execute(s.scopes.insert().values(slug="other", description="", created_at=_NOW))
    return RoutineScopeStore(hub_store_connections(engine)), routine, engine


def _relink(store: RoutineScopeStore, routine: Routine, slug: str, *, link: bool) -> Routine:
    linked = store.list_scopes(routine.routine_id)
    scopes = [*linked, slug] if link else [x for x in linked if x != slug]
    moved, change = routine.with_scopes(linked, scopes, _CTX, if_match=None, at=_NOW)  # type: ignore[misc]
    verb = store.link if link else store.unlink
    return verb(moved, slug, from_revision=routine.revision, change=change)


def test_link_then_list_scopes_round_trips_moving_the_revision(tmp_path: Path) -> None:
    store, routine, engine = _routine_scope_store(tmp_path)

    linked = _relink(store, routine, "other", link=True)

    assert store.list_scopes("rtn_1") == ["blizzard", "other"]
    assert RoutineStore(hub_store_connections(engine)).get("rtn_1").revision == linked.revision == 2  # type: ignore[union-attr]
    assert _changes(engine)[-1] == ("routine", "nightly", 2, "edit")


def test_unlink_removes_the_link(tmp_path: Path) -> None:
    store, routine, engine = _routine_scope_store(tmp_path)
    linked = _relink(store, routine, "other", link=True)

    _relink(store, linked, "other", link=False)

    assert store.list_scopes("rtn_1") == ["blizzard"]
    assert len(_changes(engine)) == 3


def test_link_from_a_moved_revision_conflicts_and_links_nothing(tmp_path: Path) -> None:
    store, routine, engine = _routine_scope_store(tmp_path)
    moved, change = routine.with_scopes(["blizzard"], ["blizzard", "other"], _CTX, if_match=None, at=_NOW)  # type: ignore[misc]

    with pytest.raises(ConfigRevisionConflict):
        store.link(moved, "other", from_revision=3, change=change)

    assert store.list_scopes("rtn_1") == ["blizzard"]
    assert len(_changes(engine)) == 1


def test_list_scopes_for_an_unlinked_routine_is_empty(tmp_path: Path) -> None:
    store, _, _ = _routine_scope_store(tmp_path)

    assert store.list_scopes("rtn_ghost") == []


def test_list_routines_returns_the_linked_routine_ids(tmp_path: Path) -> None:
    store, _, _ = _routine_scope_store(tmp_path)

    assert store.list_routines("blizzard") == ["rtn_1"]


def test_list_routines_orders_by_routine_id(tmp_path: Path) -> None:
    routine_store, engine = _store_and_engine(tmp_path)
    _create(routine_store, _routine(routine_id="rtn_b", name="b"))
    _create(routine_store, _routine(routine_id="rtn_a", name="a"))
    store = RoutineScopeStore(hub_store_connections(engine))

    assert store.list_routines("blizzard") == ["rtn_a", "rtn_b"]


def test_list_routines_for_an_unlinked_scope_is_empty(tmp_path: Path) -> None:
    store, _, _ = _routine_scope_store(tmp_path)

    assert store.list_routines("other") == []


def test_unlinking_a_pair_leaves_its_findings_readable(tmp_path: Path) -> None:
    """A finding recorded under a `(routine, scope)` pair stays readable through
    `FindingStore.list_for` after that pair is unlinked — no finding read joins through
    `routine_scopes`."""
    scope_store, routine, engine = _routine_scope_store(tmp_path)
    finding_store = FindingStore(hub_store_connections(engine))
    linked = _relink(scope_store, routine, "other", link=True)
    finding_store.add(
        "fnd_1",
        routine_name="nightly",
        scope_slug="other",
        class_="style",
        locus="src/example.py:1",
        summary="an example finding",
        introduced=None,
        at=_NOW,
    )

    _relink(scope_store, linked, "other", link=False)

    assert scope_store.list_scopes("rtn_1") == ["blizzard"]
    assert [f.finding_id for f in finding_store.list_for("nightly", "other")] == ["fnd_1"]


def test_a_create_losing_the_name_to_a_concurrent_one_raises_name_taken(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create(store, _routine(routine_id="rtn_1", name="nightly"))

    with pytest.raises(RoutineNameTakenError) as raised:
        _create(store, _routine(routine_id="rtn_2", name="nightly"))

    assert raised.value.name == "nightly"
    assert store.get("rtn_2") is None
