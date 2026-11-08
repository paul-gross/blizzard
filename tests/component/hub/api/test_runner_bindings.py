"""A runner's bindings and subscription at the hub: a registration serves the bound projects in binding order, flags
a binding whose workspace its host has not reported and a subscription outside its host's roster, and the matched peek
offers no work through a flagged binding and narrows to one project on request (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.binding_world import BindingWorld, bindings_of
from tests.support.hub_request_auth import bearer
from tests.support.scaffolding import count_queries, emitted_events


def test_a_registration_serves_the_bound_projects_in_binding_order_and_flags_what_the_host_lacks(
    tmp_path: Path,
) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])

    answer = world.register(
        projects=["ignored"], bindings=bindings_of(("second", "ws-a"), ("default", "ws-x")), subscription="max"
    )

    assert [p["entry"] for p in answer["projects"]] == ["second", "default"]
    assert [(b["project"], b["workspace_unreported"]) for b in answer["bindings"]] == [
        ("second", False),
        ("default", True),
    ]
    assert answer["subscription"] == "max"
    assert answer["subscription_unrostered"] is False


def test_a_legacy_body_is_served_from_projects_and_reports_nobindings_of(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)

    answer = world.register(projects=["second"])

    assert [p["entry"] for p in answer["projects"]] == ["second"]
    assert (answer["bindings"], answer["subscription"], answer["subscription_unrostered"]) == (None, None, False)
    assert world.peek() == [world.chunks["second"]]


def test_the_runner_views_carry_the_same_flags_as_the_answer(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-x")), subscription="other")

    detail = world.hub.client.get(f"/api/runners/{world.runner_id}").json()
    listed = next(
        r for r in world.hub.client.get("/api/runners").json()["runners"] if r["runner_id"] == world.runner_id
    )
    own = world.hub.client.get(f"/api/fleet/runners/{world.runner_id}", headers=bearer(world.token)).json()

    for view in (detail, listed, own):
        assert view["bindings"] == answer["bindings"]
        assert (view["subscription"], view["subscription_unrostered"]) == ("other", True)
    assert [b["workspace_unreported"] for b in answer["bindings"]] == [False, True]


def test_a_runner_without_a_host_and_a_host_that_never_reported_are_never_flagged(tmp_path: Path) -> None:
    (tmp_path / "alone").mkdir()
    (tmp_path / "unreported").mkdir()
    alone = BindingWorld(tmp_path / "alone", hosted=False)
    unreported = BindingWorld(tmp_path / "unreported")

    for world in (alone, unreported):
        answer = world.register(bindings=bindings_of(("default", "ws-x")), subscription="nope")
        assert answer["bindings"][0]["workspace_unreported"] is False
        assert answer["subscription_unrostered"] is False
        assert world.peek() == [world.chunks["default"]]


def test_the_peek_offers_nothing_through_a_flagged_binding_until_the_host_reports_it(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert world.peek() == [world.chunks["default"]]
    assert world.peek(projects=["second"]) == []
    assert world.peek(projects=["nope"]) == []

    world.report(["ws-a", "ws-x"])
    world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert world.peek(projects=["second"]) == [world.chunks["second"]]


def test_the_peek_withholds_a_binding_the_moment_its_workspace_leaves_the_report(tmp_path: Path) -> None:
    """Flags are read off the latest report, never stored: a stored one goes stale between registrations."""
    world = BindingWorld(tmp_path)
    world.report(["ws-a", "ws-x"])
    world.register(bindings=bindings_of(("second", "ws-x")), subscription="max")
    assert world.peek() == [world.chunks["second"]]

    world.report(["ws-a"])

    assert world.peek() == []


def test_an_unrostered_subscription_withholds_every_binding(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-a")), subscription="other")

    assert answer["subscription_unrostered"] is True
    assert world.peek() == []
    assert world.peek(projects=["default"]) == []


def test_a_project_is_withheld_only_when_every_binding_reaching_it_is(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("default", "ws-x"), ("default", "ws-a")))

    assert world.peek() == [world.chunks["default"]]


def test_projects_narrows_by_entry_or_by_resolved_id_and_offers_the_first_eligible_entry(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-a")))
    second_id = next(p["project_id"] for p in answer["projects"] if p["entry"] == "second")

    assert world.peek() == [world.chunks["default"]]
    assert world.peek(projects=["second"]) == [world.chunks["second"]]
    assert world.peek(projects=[second_id]) == [world.chunks["second"]]


def test_a_registration_without_bindings_clears_the_ones_held(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("second", "ws-x")))
    assert world.peek() == []

    answer = world.register(projects=["second"])

    assert answer["bindings"] is None
    assert world.peek() == [world.chunks["second"]]


def test_an_empty_bindings_list_serves_nothing(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)

    answer = world.register(bindings=[])

    assert (answer["projects"], answer["bindings"]) == ([], [])
    assert world.peek() == []


def test_the_peek_query_count_does_not_grow_withbindings_of(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("default", "ws-a")))
    one = count_queries(world.hub.engine, world.peek)
    world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-a")))
    two = count_queries(world.hub.engine, world.peek)

    assert one == two


def test_two_identical_registrations_publish_one_runner_frame(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    body = {"bindings": bindings_of(("default", "ws-a")), "subscription": "max"}

    def frames() -> int:
        return len([e for e in emitted_events(world.hub) if e["event"] == "runner-changed"])

    world.register(**body)
    before = frames()
    world.register(**body)

    assert frames() == before


def _claimable(world: BindingWorld) -> dict[str, bool]:
    """The ``claimable`` flag of each served entry on the runner's ``GET /api/runners`` row, by entry."""
    listed = next(
        r
        for r in world.hub.client.get("/api/runners", params={"include_retired": True}).json()["runners"]
        if r["runner_id"] == world.runner_id
    )
    return {p["entry"]: p["claimable"] for p in listed["projects"]}


def test_the_runners_read_marks_a_served_project_claimable_only_where_the_claim_path_would_take_it(
    tmp_path: Path,
) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert _claimable(world) == {"default": True, "second": False}

    paused = world.hub.client.post(f"/api/runners/{world.runner_id}/pause", json={})
    assert paused.status_code == 200, paused.text
    assert _claimable(world) == {"default": False, "second": False}

    assert world.hub.client.post(f"/api/runners/{world.runner_id}/resume", json={}).status_code == 200
    assert _claimable(world) == {"default": True, "second": False}


def test_the_runners_read_marks_nothing_claimable_under_an_unrostered_subscription(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=bindings_of(("default", "ws-a")), subscription="other")

    assert _claimable(world) == {"default": False}


def test_the_runners_read_marks_nothing_claimable_for_a_runner_that_never_connected_or_is_retired(
    tmp_path: Path,
) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    assert _claimable(world) == {}

    world.register(bindings=bindings_of(("default", "ws-a")), subscription="max")
    assert _claimable(world) == {"default": True}

    retired = world.hub.client.post(f"/api/runners/{world.runner_id}/retire", json={})
    assert retired.status_code == 200, retired.text
    assert _claimable(world) == {"default": False}


def test_the_declared_agents_are_stored_and_read_back_on_every_runner_view(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)

    world.register(bindings=bindings_of(("default", "ws-a")), agents=3)

    detail = world.hub.client.get(f"/api/runners/{world.runner_id}").json()
    listed = next(
        r for r in world.hub.client.get("/api/runners").json()["runners"] if r["runner_id"] == world.runner_id
    )
    own = world.hub.client.get(f"/api/fleet/runners/{world.runner_id}", headers=bearer(world.token)).json()
    assert [view["agents"] for view in (detail, listed, own)] == [3, 3, 3]
    assert [view["bindings"][0]["agents"] for view in (detail, listed, own)] == [3, 3, 3]


def test_a_binding_view_reads_zero_agents_when_the_runner_sent_no_count(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)

    world.register(bindings=bindings_of(("default", "ws-a")))

    detail = world.hub.client.get(f"/api/runners/{world.runner_id}").json()
    assert detail["bindings"][0]["agents"] == 0


def test_a_runner_still_sending_binding_agents_and_a_peek_project_is_served_as_if_it_did_not(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    stale = [{"project": "default", "workspace_id": "ws-a", "agents": 4}, {"project": "second", "workspace_id": "ws-a"}]

    answer = world.register(bindings=stale, agents=2)

    assert [b["agents"] for b in answer["bindings"]] == [2, 2]
    assert world.peek(project="second", projects=["default"]) == [world.chunks["default"]]


def test_a_registration_without_agents_reads_back_none_and_a_re_registration_replaces_it(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)

    world.register(bindings=bindings_of(("default", "ws-a")), agents=2)
    world.register(bindings=bindings_of(("default", "ws-a")))

    assert world.hub.client.get(f"/api/runners/{world.runner_id}").json()["agents"] is None


@pytest.mark.parametrize("agents", [0, -1])
def test_a_non_positive_agents_is_refused(tmp_path: Path, agents: int) -> None:
    world = BindingWorld(tmp_path)

    resp = world.hub.client.post(
        "/api/fleet/runners", json={"workspace_id": "ws-a", "agents": agents}, headers=bearer(world.token)
    )

    assert resp.status_code == 422, resp.text
