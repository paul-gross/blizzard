"""Routine and scope rules (unit tier, by value): which verbs each brake state allows, and
every authoring and membership rule over loaded values — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.brake import BrakeState, BrakeVerb
from blizzard.hub.domain.garden.routines import (
    Routine,
    RoutineDefaultScopeUnlinkError,
    RoutineEdit,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RoutineVerb,
    require_graph_resolves,
    require_name_free,
)
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug, ScopeVerb
from blizzard.hub.domain.graph.model import Graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CTX = ChangeContext(actor="operator", door=Door.API)
_GRAPH = Graph(graph_id="gr_1", name="default", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)
_SCOPE = Scope(slug="blizzard", description="the hub itself", created_at=_T0)
_OTHER_SCOPE = Scope(slug="runner", description="", created_at=_T0)
_ROUTINE = Routine(
    routine_id="rtn_1",
    name="gardening",
    graph_name="default",
    default_scope_slug="blizzard",
    created_at=_T0,
    default_model=["opus"],
    default_effort="high",
    default_harnesses=["claude"],
)


# --- the brake ---------------------------------------------------------------


def test_brake_state_reads_the_newest_retired_flag() -> None:
    assert BrakeState.of(retired=True) is BrakeState.RETIRED
    assert BrakeState.of(retired=False) is BrakeState.ENABLED


def test_retire_records_a_retired_fact_and_enable_a_cleared_one() -> None:
    assert BrakeVerb.RETIRE.records_retired is True
    assert BrakeVerb.ENABLE.records_retired is False


# --- the scope table ---------------------------------------------------------


def test_every_scope_verb_has_a_declared_row() -> None:
    assert set(Scope.LEGAL_FROM) == set(ScopeVerb)


@pytest.mark.parametrize(
    "verb",
    [
        ScopeVerb.CREATE,
        ScopeVerb.NAME_AS_DEFAULT,
        ScopeVerb.EDIT_DESCRIPTION,
        ScopeVerb.LINK,
        ScopeVerb.UNLINK,
        ScopeVerb.READ,
    ],
)
def test_a_retired_scope_can_still_be_named_edited_and_linked(verb: ScopeVerb) -> None:
    assert Scope.allows(verb, retired=True)
    assert Scope.allows(verb, retired=False)


def test_a_run_against_a_retired_scope_is_illegal() -> None:
    assert Scope.allows(ScopeVerb.RUN_AGAINST, retired=False)
    assert not Scope.allows(ScopeVerb.RUN_AGAINST, retired=True)


@pytest.mark.parametrize("verb", [ScopeVerb.RETIRE, ScopeVerb.ENABLE])
@pytest.mark.parametrize("retired", [True, False])
def test_scope_retire_and_enable_are_legal_from_either_state(verb: ScopeVerb, retired: bool) -> None:
    assert Scope.allows(verb, retired=retired)


# --- the routine table -------------------------------------------------------


def test_every_routine_verb_has_a_declared_row() -> None:
    assert set(Routine.LEGAL_FROM) == set(RoutineVerb)


@pytest.mark.parametrize("retired", [True, False])
def test_a_held_name_refuses_a_create_in_either_state(retired: bool) -> None:
    assert not Routine.allows(RoutineVerb.CREATE, retired=retired)


@pytest.mark.parametrize(
    "verb",
    [
        RoutineVerb.EDIT,
        RoutineVerb.RETIRE,
        RoutineVerb.ENABLE,
        RoutineVerb.LINK_SCOPE,
        RoutineVerb.UNLINK_SCOPE,
        RoutineVerb.READ,
    ],
)
def test_a_retired_routine_refuses_nothing_but_a_run(verb: RoutineVerb) -> None:
    assert Routine.allows(verb, retired=True)
    assert Routine.allows(verb, retired=False)


def test_a_run_of_a_retired_routine_is_illegal() -> None:
    assert Routine.allows(RoutineVerb.RUN, retired=False)
    assert not Routine.allows(RoutineVerb.RUN, retired=True)


# --- authoring rules ---------------------------------------------------------


def test_a_free_name_passes() -> None:
    require_name_free(None, "gardening")


def test_a_held_name_refuses_naming_it() -> None:
    with pytest.raises(RoutineNameTakenError) as raised:
        require_name_free(_ROUTINE, "gardening")
    assert raised.value.name == "gardening"


def test_a_resolved_graph_passes_through() -> None:
    assert require_graph_resolves(_GRAPH, "default") is _GRAPH


def test_an_unresolved_graph_refuses_naming_it() -> None:
    with pytest.raises(RoutineGraphUnresolvedError) as raised:
        require_graph_resolves(None, "missing")
    assert raised.value.graph_name == "missing"


def test_new_points_at_the_default_scope_and_stamps_the_instant() -> None:
    model = ["opus"]
    minted, change = Routine.new(
        routine_id="rtn_9",
        name="nightly",
        graph_name="default",
        default_scope=_SCOPE,
        default_model=model,
        default_effort=None,
        default_harnesses=("claude",),
        ctx=_CTX,
        at=_T0,
    )
    assert minted == Routine(
        routine_id="rtn_9",
        name="nightly",
        graph_name="default",
        default_scope_slug="blizzard",
        created_at=_T0,
        default_model=["opus"],
        default_effort=None,
        default_harnesses=["claude"],
    )
    assert minted.default_model is not model
    assert (change.record_key, change.revision) == ("nightly", 1)


def test_an_edit_keeping_the_name_yields_the_record_to_write() -> None:
    decided = _ROUTINE.edit(
        RoutineEdit(name="gardening", graph_name="other", default_scope_slug=ScopeSlug.parse("runner")),
        _CTX,
        if_match=None,
        at=_T0,
    )
    assert decided is not None
    edited, _ = decided
    assert edited == replace(_ROUTINE, graph_name="other", default_scope_slug="runner", revision=2)


def test_an_edit_renaming_the_routine_refuses_naming_the_current_name() -> None:
    with pytest.raises(RoutineNameImmutableError) as raised:
        _ROUTINE.edit(RoutineEdit(name="renamed"), _CTX, if_match=None, at=_T0)
    assert raised.value.current_name == "gardening"


def test_the_default_scope_cannot_be_unlinked() -> None:
    with pytest.raises(RoutineDefaultScopeUnlinkError) as raised:
        _ROUTINE.require_unlinkable(_SCOPE)
    assert (raised.value.routine_id, raised.value.scope_slug) == ("rtn_1", "blizzard")


def test_any_other_scope_can_be_unlinked() -> None:
    _ROUTINE.require_unlinkable(_OTHER_SCOPE)


def test_a_run_acts_on_the_override_when_one_is_named() -> None:
    assert _ROUTINE.effective_scope_slug(ScopeSlug.parse("runner")) == ScopeSlug("runner")


def test_a_run_acts_on_the_default_scope_otherwise() -> None:
    assert _ROUTINE.effective_scope_slug(None) == ScopeSlug("blizzard")
