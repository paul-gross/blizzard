"""Routine-run rules (unit tier, by value): each refusal, the mode settlement, and the
whole run plan over already-loaded values — no repository, no clock."""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime

import pytest

from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.garden.findings.model import FindingSet
from blizzard.hub.domain.garden.routines import Routine, RoutineGraphUnresolvedError
from blizzard.hub.domain.garden.runs.run import (
    RoutineRetiredError,
    RunPlan,
    ScopeNotRelatedError,
    ScopeRetiredError,
    compose_charge,
    plan_run,
    require_related,
    require_routine_runnable,
    require_scope_open,
    settle_mode,
)
from blizzard.hub.domain.garden.scopes import Scope
from blizzard.hub.domain.graph.model import Graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_GRAPH = Graph(graph_id="gr_1", name="default", entry_node_id="nd_1", nodes=[], edges=[], created_at=_T0)
_ROUTINE = Routine(
    routine_id="rtn_1", name="gardening", graph_name="default", default_scope_slug="blizzard", created_at=_T0
)
_SCOPE = Scope(slug="blizzard", description="the hub itself", created_at=_T0)
_BASELINE = FindingSet(
    finding_set_id="fins_1",
    artifact_id="art_1",
    chunk_id="ch_prior",
    scope_slug="blizzard",
    routine_name="gardening",
    revisions={"blizzard": "a1b2c3d"},
    measurement=None,
)


def _plan(
    *,
    routine_retired: bool = False,
    graph: Graph | None = _GRAPH,
    scope_retired: bool = False,
    linked: Collection[str] = ("blizzard",),
    requested_mode: RunMode = RunMode.FULL,
    baseline: FindingSet | None = None,
    note: str | None = None,
) -> RunPlan:
    return plan_run(
        _ROUTINE,
        routine_retired=routine_retired,
        graph=graph,
        scope=_SCOPE,
        scope_retired=scope_retired,
        linked=linked,
        requested_mode=requested_mode,
        baseline=baseline,
        note=note,
    )


def test_an_enabled_routine_is_runnable() -> None:
    require_routine_runnable(_ROUTINE, retired=False)


def test_a_retired_routine_refuses_naming_it() -> None:
    with pytest.raises(RoutineRetiredError) as raised:
        require_routine_runnable(_ROUTINE, retired=True)
    assert raised.value.name == "gardening"


def test_a_linked_scope_is_related() -> None:
    require_related(_ROUTINE, _SCOPE, {"blizzard", "runner"})


def test_an_unlinked_scope_refuses_naming_the_pair() -> None:
    with pytest.raises(ScopeNotRelatedError) as raised:
        require_related(_ROUTINE, _SCOPE, ["runner"])
    assert (raised.value.routine_id, raised.value.slug) == ("rtn_1", "blizzard")


def test_an_enabled_scope_is_open() -> None:
    require_scope_open(_SCOPE, retired=False)


def test_a_retired_scope_refuses_naming_it() -> None:
    with pytest.raises(ScopeRetiredError) as raised:
        require_scope_open(_SCOPE, retired=True)
    assert raised.value.slug == "blizzard"


@pytest.mark.parametrize(
    ("requested", "baseline", "expected"),
    [
        (RunMode.FULL, None, (RunMode.FULL, False)),
        (RunMode.FULL, _BASELINE, (RunMode.FULL, False)),
        (RunMode.DELTA, _BASELINE, (RunMode.DELTA, False)),
        (RunMode.DELTA, None, (RunMode.FULL, True)),
    ],
)
def test_settle_mode(requested: RunMode, baseline: FindingSet | None, expected: tuple[RunMode, bool]) -> None:
    assert settle_mode(requested, baseline) == expected


def test_a_plan_carries_the_graph_title_and_charge() -> None:
    plan = _plan(note="look at the store")
    assert plan == RunPlan(
        graph=_GRAPH,
        effective_mode=RunMode.FULL,
        downgraded=False,
        baseline=None,
        title="gardening run (full)",
        charge=compose_charge(
            routine_name="gardening",
            graph_name="default",
            scope_slug="blizzard",
            scope_description="the hub itself",
            mode=RunMode.FULL,
            downgraded=False,
            baseline=None,
            note="look at the store",
        ),
    )


def test_a_downgraded_plan_is_titled_by_its_effective_mode() -> None:
    plan = _plan(requested_mode=RunMode.DELTA)
    assert (plan.effective_mode, plan.downgraded, plan.title) == (RunMode.FULL, True, "gardening run (full)")


def test_a_delta_plan_carries_its_baseline() -> None:
    plan = _plan(requested_mode=RunMode.DELTA, baseline=_BASELINE)
    assert (plan.effective_mode, plan.baseline, plan.title) == (RunMode.DELTA, _BASELINE, "gardening run (delta)")


def test_a_retired_routine_refuses_before_the_graph() -> None:
    with pytest.raises(RoutineRetiredError):
        _plan(routine_retired=True, graph=None, linked=[], scope_retired=True)


def test_an_unresolved_graph_refuses_before_the_related_check() -> None:
    with pytest.raises(RoutineGraphUnresolvedError):
        _plan(graph=None, linked=[], scope_retired=True)


def test_an_unrelated_scope_refuses_before_the_retired_scope_check() -> None:
    with pytest.raises(ScopeNotRelatedError):
        _plan(linked=[], scope_retired=True)


def test_a_retired_related_scope_refuses() -> None:
    with pytest.raises(ScopeRetiredError):
        _plan(scope_retired=True)
