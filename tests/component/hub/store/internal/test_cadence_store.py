"""``CadenceStore`` — the cadence repository (component tier), driven through the cadence verbs on a production-wired
hub over a migrated-to-head sqlite store.

A cadence reads back as written; each write moves the revision by compare-and-set and commits its change row in the
same transaction; retirement is appended lifecycle facts, the newest deciding; a chore's scope rows exist only for a
listed chore; and a chore keeps its id while its key stays, a key dropped and written again minting a new one."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.foundation.cadence_anchor import CadenceAnchor
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.model import (
    Cadence,
    CadenceEdit,
    CadenceShape,
    CadenceSlugTakenError,
    ChoreShape,
    ScheduleShape,
    ScopeSelection,
)
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, RecordKind
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.cadence_store import CadenceStore
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_project,
    hub_scoped,
    hub_store_connections,
    project_record,
)

_CTX = ChangeContext(actor="operator", door=Door.API)

_SHAPE = CadenceShape(
    slug="evening",
    name="Evening sweep",
    schedule=ScheduleShape("0 20 * * *", "America/Chicago"),
    spacing="10d",
    anchor="finished",
    chores=(
        ChoreShape("arch", "architecture-sweep", scopes="all", mode="delta", note="Weigh the lanes."),
        ChoreShape("tests", "test-gaps", scopes=["runner", "hub"], promote=False),
        ChoreShape("plain", "test-gaps"),
    ),
)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _create(hub: HubHarness, shape: CadenceShape = _SHAPE) -> Cadence:
    return hub.services.cadence_authoring.create(
        shape, project=project_record(hub, default_project(hub.engine)), ctx=_CTX, may_admit=True
    )


def _edit(hub: HubHarness, cadence: Cadence, edit: CadenceEdit) -> Cadence:
    return hub.services.cadence_authoring.edit(cadence, edit, _CTX, may_admit=True, if_match=cadence.revision)


def _rows(hub: HubHarness, table: sa.Table) -> list[sa.Row[Any]]:
    with hub.engine.connect() as conn:
        return list(conn.execute(hub_scoped(sa.select(table))))


def _changes(hub: HubHarness) -> list[sa.Row[Any]]:
    with hub.engine.connect() as conn:
        return list(
            conn.execute(
                hub_scoped(
                    sa.select(s.config_changes).where(s.config_changes.c.record_kind == RecordKind.CADENCE.value)
                ).order_by(s.config_changes.c.id)
            )
        )


def test_a_cadence_reads_back_as_written_by_id_slug_and_list(hub: HubHarness) -> None:
    created = _create(hub)
    project = default_project(hub.engine)

    by_id = hub.services.cadences.get(created.cadence_id)

    assert by_id == created
    assert hub.services.cadences.get_by_slug(project, "evening") == created
    assert hub.services.cadences.list_all(project) == [created]
    assert hub.services.cadences.list_all() == [created]
    assert by_id is not None
    assert created.cadence_id.startswith("cad_")
    assert (by_id.name, by_id.revision, by_id.retired) == ("Evening sweep", 1, False)
    assert by_id.rhythm.schedule is not None
    assert by_id.rhythm.spacing is not None
    assert (by_id.rhythm.schedule.cron, by_id.rhythm.schedule.zone) == ("0 20 * * *", "America/Chicago")
    assert (by_id.rhythm.spacing.minutes, by_id.rhythm.anchor) == (10 * 24 * 60, CadenceAnchor.FINISHED)
    assert [chore.key for chore in by_id.chores] == ["arch", "tests", "plain"]
    arch, tests, plain = (chore.spec for chore in by_id.chores)
    assert (arch.selection, arch.mode, arch.note, arch.promote) == (
        ScopeSelection.ALL,
        RunMode.DELTA,
        "Weigh the lanes.",
        True,
    )
    assert (tests.selection, tests.scopes, tests.promote) == (ScopeSelection.LISTED, ("hub", "runner"), False)
    assert (plain.selection, plain.scopes, plain.mode) == (ScopeSelection.DEFAULT, (), RunMode.FULL)
    assert all(chore.chore_id.startswith("chr_") for chore in by_id.chores)


def test_an_unknown_cadence_reads_as_none(hub: HubHarness) -> None:
    assert hub.services.cadences.get("cad_missing") is None
    assert hub.services.cadences.get_by_slug(default_project(hub.engine), "missing") is None
    assert hub.services.cadences.list_all() == []


def test_a_slug_its_project_holds_refuses_a_create_retired_cadences_included(hub: HubHarness) -> None:
    held = _create(hub)
    hub.services.cadence_lifecycle.retire(held, _CTX, by="operator")

    with pytest.raises(CadenceSlugTakenError):
        _create(hub, replace(_SHAPE, name="another"))


def test_a_create_that_loses_the_slug_race_reads_as_the_slug_refusal(hub: HubHarness) -> None:
    held = _create(hub)
    store = CadenceStore(hub_store_connections(hub.engine))
    twin, change = Cadence.new(replace(_SHAPE, chores=()), project_id=held.project_id, ctx=_CTX, at=hub.clock.now())

    with pytest.raises(CadenceSlugTakenError):
        store.create(twin, change=change)

    assert len(_changes(hub)) == 1


def test_only_a_listed_chore_holds_scope_rows(hub: HubHarness) -> None:
    created = _create(hub)
    tests = next(chore for chore in created.chores if chore.key == "tests")

    rows = _rows(hub, s.cadence_chore_scopes)

    assert sorted((row.chore_id, row.scope_slug) for row in rows) == [
        (tests.chore_id, "hub"),
        (tests.chore_id, "runner"),
    ]
    edited = _edit(
        hub, created, CadenceEdit(chores=[ChoreShape("tests", "test-gaps", scopes="all"), ChoreShape("plain", "x")])
    )
    assert _rows(hub, s.cadence_chore_scopes) == []
    assert hub.services.cadences.get(created.cadence_id) == edited


def test_an_edit_moves_the_revision_and_commits_its_change_row(hub: HubHarness) -> None:
    created = _create(hub)

    edited = _edit(hub, created, CadenceEdit(name="Renamed", spacing=None, anchor=None))

    assert edited.revision == 2
    assert hub.services.cadences.get(created.cadence_id) == edited
    assert edited.rhythm.spacing is None
    create, edit = _changes(hub)
    assert (create.op, create.revision, create.record_key, create.project_id) == (
        ChangeOp.CREATE.value,
        1,
        "evening",
        created.project_id,
    )
    assert (edit.op, edit.revision) == (ChangeOp.EDIT.value, 2)


def test_a_stale_revision_is_refused_and_writes_nothing(hub: HubHarness) -> None:
    created = _create(hub)
    _edit(hub, created, CadenceEdit(name="First"))
    store = CadenceStore(hub_store_connections(hub.engine))
    decided = created.edit(CadenceEdit(name="Second"), _CTX, if_match=None, at=hub.clock.now())
    assert decided is not None
    stale, change = decided

    with pytest.raises(ConfigRevisionConflict):
        store.update(stale, from_revision=created.revision, change=change)

    stored = hub.services.cadences.get(created.cadence_id)
    assert stored is not None
    assert (stored.name, stored.revision) == ("First", 2)
    assert len(_changes(hub)) == 2


def test_an_edit_naming_the_stored_revision_wrongly_is_refused_before_writing(hub: HubHarness) -> None:
    created = _create(hub)

    with pytest.raises(ConfigRevisionConflict):
        hub.services.cadence_authoring.edit(created, CadenceEdit(name="x"), _CTX, may_admit=True, if_match=7)


def test_retirement_is_appended_facts_the_newest_deciding(hub: HubHarness) -> None:
    created = _create(hub)

    retired = hub.services.cadence_lifecycle.retire(created, _CTX, by="operator")
    again = hub.services.cadence_lifecycle.retire(retired, _CTX, by="operator")
    enabled = hub.services.cadence_lifecycle.enable(again, _CTX, by="operator", may_admit=True)

    assert again is retired
    stored = hub.services.cadences.get(created.cadence_id)
    assert stored is not None
    assert (stored.retired, stored.revision) == (False, 3) == (enabled.retired, enabled.revision)
    facts = _rows(hub, s.cadence_lifecycle_facts)
    assert [(row.retired, row.set_by) for row in sorted(facts, key=lambda r: r.id)] == [
        (True, "operator"),
        (False, "operator"),
    ]
    assert [row.op for row in _changes(hub)] == ["create", "retire", "enable"]
    hub.services.cadence_lifecycle.retire(enabled, _CTX, by="operator")
    stored = hub.services.cadences.get(created.cadence_id)
    assert stored is not None
    assert stored.retired


def test_an_edit_keeping_a_chores_key_keeps_its_id_and_a_key_dropped_and_rewritten_mints_anew(
    hub: HubHarness,
) -> None:
    created = _create(hub)
    ids = {chore.key: chore.chore_id for chore in created.chores}
    hub.clock.advance(timedelta(minutes=5))

    kept = _edit(
        hub,
        created,
        CadenceEdit(
            chores=[
                ChoreShape("arch", "another-routine", scopes="all", mode="full", promote=False),
                ChoreShape("tests", "test-gaps", scopes=["hub"]),
            ]
        ),
    )
    dropped = {chore.key: chore for chore in kept.chores}
    assert {key: chore.chore_id for key, chore in dropped.items()} == {"arch": ids["arch"], "tests": ids["tests"]}
    assert dropped["arch"].scopes_set_at == created.chores[0].scopes_set_at
    assert dropped["tests"].scopes_set_at == hub.clock.now()
    assert {row.chore_id for row in _rows(hub, s.cadence_chores)} == {ids["arch"], ids["tests"]}

    rewritten = _edit(hub, kept, CadenceEdit(chores=[*_SHAPE.chores]))

    by_key = {chore.key: chore.chore_id for chore in rewritten.chores}
    assert by_key["arch"] == ids["arch"]
    assert by_key["plain"] != ids["plain"]
    assert by_key["plain"].startswith("chr_")
    assert hub.services.cadences.get(created.cadence_id) == rewritten
    diff_fields = [entry["field"] for entry in json.loads(_changes(hub)[1].diff)]
    assert diff_fields == ["chores.arch", "chores.tests", "chores.plain"]
