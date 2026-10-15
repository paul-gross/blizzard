"""The routing map and each binding's slot count, pinned by value."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.environments.repository import OpenLeaseHold
from blizzard.runner.hub.client import BindingDeclaration
from blizzard.runner.hub.served_projects import ServedProject, ServedProjectsAnswer
from blizzard.runner.lifecycle.model import SlotBinding, binding_usage, open_lease_count
from blizzard.runner.lifecycle.routing import RoutedBinding, RoutingConflict, routing_map

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)


def _answer(*projects: ServedProject) -> ServedProjectsAnswer:
    return ServedProjectsAnswer(projects=projects, answered_at=_AT)


# --- routing_map ------------------------------------------------------------------------


def test_each_binding_routes_the_project_its_declared_entry_resolved_to_in_configuration_order() -> None:
    bindings = (BindingDeclaration("web", "ws_b", 2), BindingDeclaration("pr_api", "ws_a", 1))
    answer = _answer(ServedProject("pr_api", project_id="pr_api"), ServedProject("web", project_id="pr_web"))

    routing = routing_map(bindings, answer)

    assert routing.claims() == (
        RoutedBinding(project="web", project_id="pr_web", workspace_id="ws_b", agents=2),
        RoutedBinding(project="pr_api", project_id="pr_api", workspace_id="ws_a", agents=1),
    )
    assert routing.conflicts == ()
    assert (routing.project_id("web"), routing.project_id("pr_api")) == ("pr_web", "pr_api")


def test_an_unresolved_or_unanswered_entry_routes_nothing() -> None:
    bindings = (
        BindingDeclaration("gone", "ws_a", 1),
        BindingDeclaration("later", "ws_a", 1),
        BindingDeclaration("web", "ws_a", 1),
    )
    routing = routing_map(bindings, _answer(ServedProject("gone"), ServedProject("web", project_id="pr_web")))

    assert [route.project for route in routing.claims()] == ["web"]
    assert routing.project_id("gone") is None and routing.project_id("later") is None


def test_no_answer_yet_routes_nothing() -> None:
    assert routing_map((BindingDeclaration("web", "ws_a", 1),), None).claims() == ()


def test_two_entries_resolving_to_one_project_are_a_conflict_and_route_no_claim() -> None:
    bindings = (
        BindingDeclaration("web", "ws_a", 1),
        BindingDeclaration("pr_web", "ws_b", 1),
        BindingDeclaration("api", "ws_a", 1),
    )
    answer = _answer(
        ServedProject("web", project_id="pr_web"),
        ServedProject("pr_web", project_id="pr_web"),
        ServedProject("api", project_id="pr_api"),
    )

    routing = routing_map(bindings, answer)

    assert routing.conflicts == (RoutingConflict(project_id="pr_web", entries=("web", "pr_web")),)
    assert routing.claims() == ()


# --- binding_usage ----------------------------------------------------------------------

_A = SlotBinding(workspace_id="ws_a", project_id="pr_a")
_B = SlotBinding(workspace_id="ws_a", project_id="pr_b")
_C = SlotBinding(workspace_id="ws_c", project_id="pr_c")


def test_a_held_row_counts_against_the_binding_of_the_project_it_names() -> None:
    holds = [OpenLeaseHold("ch_1", held=True, workspace_id="ws_a", project_id="pr_a")]
    assert binding_usage([_A, _B, _C], holds) == (1, 0, 0)


def test_a_row_of_unknown_project_counts_against_every_binding_on_its_workspace() -> None:
    holds = [OpenLeaseHold("ch_1", held=True, workspace_id="ws_a", project_id=None)]
    assert binding_usage([_A, _B, _C], holds) == (1, 1, 0)


def test_a_row_of_unknown_workspace_counts_against_every_binding() -> None:
    holds = [OpenLeaseHold("ch_1", held=True, workspace_id=None, project_id="pr_a")]
    assert binding_usage([_A, _B, _C], holds) == (1, 1, 1)


def test_an_open_lease_holding_no_binding_counts_against_every_binding() -> None:
    assert binding_usage([_A, _B, _C], [OpenLeaseHold("ch_1", held=False)]) == (1, 1, 1)


def test_an_open_lease_counts_once_per_chunk_however_many_environments_it_holds() -> None:
    holds = [
        OpenLeaseHold("ch_1", held=True, workspace_id="ws_a", project_id="pr_a"),
        OpenLeaseHold("ch_1", held=True, workspace_id="ws_a", project_id="pr_a"),
        OpenLeaseHold("ch_2", held=True, workspace_id="ws_c", project_id="pr_c"),
    ]
    assert binding_usage([_A, _B, _C], holds) == (1, 0, 1)
    assert open_lease_count(holds) == 2


def test_an_unresolved_binding_counts_only_rows_that_may_be_its_own() -> None:
    unresolved = SlotBinding(workspace_id="ws_a", project_id=None)
    holds = [
        OpenLeaseHold("ch_1", held=True, workspace_id="ws_a", project_id="pr_a"),
        OpenLeaseHold("ch_2", held=True, workspace_id="ws_a", project_id=None),
    ]
    assert binding_usage([unresolved], holds) == (1,)
