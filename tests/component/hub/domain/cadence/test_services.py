"""The cadence verbs on a production-wired hub (component tier): a write leaving a live cadence admitting work needs
``may_admit``, while retire and a write that admits nothing never ask it; and attention is resolved for a whole set of
cadences against the routines, links, graphs, and scopes as they stand, in a number of queries that does not grow
with the set."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.cadence_attention import AttentionReason
from blizzard.hub.domain.cadence.model import (
    Attention,
    Cadence,
    CadenceAdmissionRefused,
    CadenceEdit,
    CadenceShape,
    ChoreShape,
)
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import ScopeSlug
from tests.support.scaffolding import HubHarness, build_hub, count_queries, default_project, project_record

_CTX = ChangeContext(actor="operator", door=Door.API)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _project(hub: HubHarness) -> str:
    return default_project(hub.engine)


def _routine(hub: HubHarness, slug: str, scope: str) -> Routine:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    return hub.services.routine_authoring.create(
        project=project_record(hub, _project(hub)),
        ctx=_CTX,
        slug=slug,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse(scope),
    )


def _shape(slug: str, *chores: ChoreShape) -> CadenceShape:
    return CadenceShape(slug=slug, spacing="1d", chores=chores)


def _create(hub: HubHarness, shape: CadenceShape, *, may_admit: bool = True) -> Cadence:
    return hub.services.cadence_authoring.create(
        shape, project=project_record(hub, _project(hub)), ctx=_CTX, may_admit=may_admit
    )


def test_a_create_keeping_a_promoting_chore_needs_may_admit(hub: HubHarness) -> None:
    with pytest.raises(CadenceAdmissionRefused):
        _create(hub, _shape("promoting", ChoreShape("a", "r")), may_admit=False)

    assert hub.services.cadences.list_all() == []
    held = _create(hub, _shape("held", ChoreShape("a", "r", promote=False)), may_admit=False)
    assert held.slug == "held"


def test_an_edit_or_enable_leaving_a_live_cadence_promoting_needs_may_admit(hub: HubHarness) -> None:
    held = _create(hub, _shape("held", ChoreShape("a", "r", promote=False)), may_admit=False)
    promote = CadenceEdit(chores=[ChoreShape("a", "r")])

    with pytest.raises(CadenceAdmissionRefused):
        hub.services.cadence_authoring.edit(held, promote, _CTX, may_admit=False)

    retired = hub.services.cadence_lifecycle.retire(held, _CTX, by="operator")
    promoting_while_retired = hub.services.cadence_authoring.edit(retired, promote, _CTX, may_admit=False)
    assert promoting_while_retired.chores[0].spec.promote
    with pytest.raises(CadenceAdmissionRefused):
        hub.services.cadence_lifecycle.enable(promoting_while_retired, _CTX, by="operator", may_admit=False)
    enabled = hub.services.cadence_lifecycle.enable(promoting_while_retired, _CTX, by="operator", may_admit=True)
    assert not enabled.retired


def test_retire_and_a_write_changing_nothing_never_ask_may_admit(hub: HubHarness) -> None:
    promoting = _create(hub, _shape("promoting", ChoreShape("a", "r")))

    unchanged = hub.services.cadence_authoring.edit(
        promoting, CadenceEdit(chores=[ChoreShape("a", "r")]), _CTX, may_admit=False
    )
    retired = hub.services.cadence_lifecycle.retire(promoting, _CTX, by="operator")

    assert unchanged is promoting
    assert retired.retired


def test_attention_names_what_stops_each_errand_and_a_retired_cadence_needs_none(hub: HubHarness) -> None:
    _routine(hub, "sweep", "blizzard")
    project = project_record(hub, _project(hub))
    hub.services.scope_registry.ensure(project, ScopeSlug.parse("unlinked"), _CTX, description="")
    attended = _create(
        hub,
        _shape(
            "attended",
            ChoreShape("fine", "sweep", scopes=["blizzard"]),
            ChoreShape("ghost", "nope"),
            ChoreShape("loose", "sweep", scopes=["blizzard", "unlinked", "missing"]),
        ),
    )
    empty = _create(hub, _shape("empty"))
    gone = hub.services.cadence_lifecycle.retire(_create(hub, _shape("gone", ChoreShape("x", "nope"))), _CTX, by="op")

    views = {
        view.cadence.slug: view.attention for view in hub.services.cadence_reading.list_cadences(include_retired=True)
    }

    assert views == {
        "attended": (
            Attention(AttentionReason.ROUTINE_UNKNOWN, "ghost"),
            Attention(AttentionReason.SCOPE_UNKNOWN, "loose", "missing"),
            Attention(AttentionReason.SCOPE_UNLINKED, "loose", "unlinked"),
        ),
        "empty": (Attention(AttentionReason.NO_CHORES),),
        "gone": (),
    }
    assert {view.cadence.slug for view in hub.services.cadence_reading.list_cadences()} == {"empty", "attended"}
    shown = hub.services.cadence_reading.show(attended.cadence_id)
    assert shown is not None and shown.attention == views["attended"]
    assert hub.services.cadence_reading.view(empty).attention == views["empty"]
    assert hub.services.cadence_reading.show(gone.cadence_id) is not None
    assert hub.services.cadence_reading.show("cad_missing") is None


def _seed(hub: HubHarness, count: int, start: int) -> None:
    for n in range(start, start + count):
        _routine(hub, f"routine-{n}", f"scope-{n}")
        _create(
            hub,
            _shape(
                f"cadence-{n}",
                ChoreShape("own", f"routine-{n}", scopes=[f"scope-{n}"]),
                ChoreShape("every", f"routine-{n}", scopes="all"),
                ChoreShape("ghost", f"missing-{n}"),
            ),
        )


def test_a_listing_reads_in_a_number_of_queries_flat_in_its_cadences(hub: HubHarness) -> None:
    _seed(hub, 2, 0)
    few = count_queries(hub.engine, lambda: hub.services.cadence_reading.list_cadences())
    _seed(hub, 6, 2)
    many = count_queries(hub.engine, lambda: hub.services.cadence_reading.list_cadences())

    assert many == few
    assert len(hub.services.cadence_reading.list_cadences()) == 8
