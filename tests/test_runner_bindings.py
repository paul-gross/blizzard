"""A runner's bindings and subscription at the hub: a registration serves the bound projects in binding order, flags
a binding whose workspace its host has not reported and a subscription outside its host's roster, and the matched peek
offers no work through a flagged binding and narrows to one project on request (component tier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.support import count_queries, emitted_events
from tests.test_fleet_auth import _bearer
from tests.test_ingest_project_api import _ingest, _two_projects

pytestmark = pytest.mark.component

_CAPABILITIES = [{"harness_id": "claude", "default": True}]
_REPORT: dict[str, Any] = {
    "name": "box",
    "workspaces": [{"workspace_id": "ws-a", "env_capacity": 2}],
    "subscriptions": [{"slug": "max", "name": "Max", "provider": "anthropic"}],
}


class _World:
    """A hub holding two projects, each with one ready chunk, and a runner under a host."""

    def __init__(self, tmp_path: Path, *, hosted: bool = True) -> None:
        self.hub, _, _ = _two_projects(tmp_path)
        self.chunks: dict[str, str] = {}
        for slug, token in (("default", "a:1"), ("second", "b:1")):
            _, body = _ingest(self.hub, token)
            self.chunks[slug] = body["chunk_id"]
            assert self.hub.client.post(f"/api/chunks/{body['chunk_id']}/promote").status_code == 202
        self.host = self.hub.client.post("/api/hosts", json={"name": "box"}).json()
        added = self.hub.client.post(
            "/api/runners", json={"name": "r", **({"host_id": self.host["host_id"]} if hosted else {})}
        )
        assert added.status_code == 201, added.text
        self.runner_id = added.json()["runner_id"]
        self.token = added.json()["token"]

    def report(self, workspaces: list[str], subscriptions: list[str] | None = None) -> None:
        body = {
            **_REPORT,
            "workspaces": [{"workspace_id": w} for w in workspaces],
            "subscriptions": [
                {"slug": s, "name": s, "provider": "anthropic"}
                for s in (["max"] if subscriptions is None else subscriptions)
            ],
        }
        resp = self.hub.client.post("/api/fleet/host", json=body, headers=_bearer(self.host["token"]))
        assert resp.status_code == 200, resp.text

    def register(self, **body: Any) -> dict[str, Any]:
        resp = self.hub.client.post(
            "/api/fleet/runners", json={"workspace_id": "ws-a", **body}, headers=_bearer(self.token)
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def peek(self, **body: Any) -> list[str]:
        resp = self.hub.client.post(
            "/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES, **body}, headers=_bearer(self.token)
        )
        assert resp.status_code == 200, resp.text
        return [e["chunk_id"] for e in resp.json()["entries"]]


def _bindings(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"project": project, "workspace_id": workspace, "agents": 1} for project, workspace in pairs]


def test_a_registration_serves_the_bound_projects_in_binding_order_and_flags_what_the_host_lacks(
    tmp_path: Path,
) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])

    answer = world.register(
        projects=["ignored"], bindings=_bindings(("second", "ws-a"), ("default", "ws-x")), subscription="max"
    )

    assert [p["entry"] for p in answer["projects"]] == ["second", "default"]
    assert [(b["project"], b["workspace_unreported"]) for b in answer["bindings"]] == [
        ("second", False),
        ("default", True),
    ]
    assert answer["subscription"] == "max"
    assert answer["subscription_unrostered"] is False


def test_a_legacy_body_is_served_from_projects_and_reports_no_bindings(tmp_path: Path) -> None:
    world = _World(tmp_path)

    answer = world.register(projects=["second"])

    assert [p["entry"] for p in answer["projects"]] == ["second"]
    assert (answer["bindings"], answer["subscription"], answer["subscription_unrostered"]) == (None, None, False)
    assert world.peek() == [world.chunks["second"]]


def test_the_runner_views_carry_the_same_flags_as_the_answer(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-x")), subscription="other")

    detail = world.hub.client.get(f"/api/runners/{world.runner_id}").json()
    listed = next(
        r for r in world.hub.client.get("/api/runners").json()["runners"] if r["runner_id"] == world.runner_id
    )
    own = world.hub.client.get(f"/api/fleet/runners/{world.runner_id}", headers=_bearer(world.token)).json()

    for view in (detail, listed, own):
        assert view["bindings"] == answer["bindings"]
        assert (view["subscription"], view["subscription_unrostered"]) == ("other", True)
    assert [b["workspace_unreported"] for b in answer["bindings"]] == [False, True]


def test_a_runner_without_a_host_and_a_host_that_never_reported_are_never_flagged(tmp_path: Path) -> None:
    (tmp_path / "alone").mkdir()
    (tmp_path / "unreported").mkdir()
    alone = _World(tmp_path / "alone", hosted=False)
    unreported = _World(tmp_path / "unreported")

    for world in (alone, unreported):
        answer = world.register(bindings=_bindings(("default", "ws-x")), subscription="nope")
        assert answer["bindings"][0]["workspace_unreported"] is False
        assert answer["subscription_unrostered"] is False
        assert world.peek() == [world.chunks["default"]]


def test_the_peek_offers_nothing_through_a_flagged_binding_until_the_host_reports_it(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert world.peek() == [world.chunks["default"]]
    assert world.peek(project="second") == []
    assert world.peek(project="nope") == []

    world.report(["ws-a", "ws-x"])
    world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert world.peek(project="second") == [world.chunks["second"]]


def test_the_peek_withholds_a_binding_the_moment_its_workspace_leaves_the_report(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a", "ws-x"])
    world.register(bindings=_bindings(("second", "ws-x")), subscription="max")
    assert world.peek() == [world.chunks["second"]]

    world.report(["ws-a"])

    assert world.peek() == []


def test_an_unrostered_subscription_withholds_every_binding(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-a")), subscription="other")

    assert answer["subscription_unrostered"] is True
    assert world.peek() == []
    assert world.peek(project="default") == []


def test_a_project_is_withheld_only_when_every_binding_reaching_it_is(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("default", "ws-x"), ("default", "ws-a")))

    assert world.peek() == [world.chunks["default"]]


def test_project_narrows_by_entry_or_by_resolved_id_and_offers_the_first_eligible_entry(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    answer = world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-a")))
    second_id = next(p["project_id"] for p in answer["projects"] if p["entry"] == "second")

    assert world.peek() == [world.chunks["default"]]
    assert world.peek(project="second") == [world.chunks["second"]]
    assert world.peek(project=second_id) == [world.chunks["second"]]


def test_a_registration_without_bindings_clears_the_ones_held(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("second", "ws-x")))
    assert world.peek() == []

    answer = world.register(projects=["second"])

    assert answer["bindings"] is None
    assert world.peek() == [world.chunks["second"]]


def test_an_empty_bindings_list_serves_nothing(tmp_path: Path) -> None:
    world = _World(tmp_path)

    answer = world.register(bindings=[])

    assert (answer["projects"], answer["bindings"]) == ([], [])
    assert world.peek() == []


def test_the_peek_query_count_does_not_grow_with_bindings(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("default", "ws-a")))
    one = count_queries(world.hub.engine, world.peek)
    world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-a")))
    two = count_queries(world.hub.engine, world.peek)

    assert one == two


def test_two_identical_registrations_publish_one_runner_frame(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    body = {"bindings": _bindings(("default", "ws-a")), "subscription": "max"}

    def frames() -> int:
        return len([e for e in emitted_events(world.hub) if e["event"] == "runner-changed"])

    world.register(**body)
    before = frames()
    world.register(**body)

    assert frames() == before


def _claimable(world: _World) -> dict[str, bool]:
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
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("default", "ws-a"), ("second", "ws-x")), subscription="max")

    assert _claimable(world) == {"default": True, "second": False}

    paused = world.hub.client.post(f"/api/runners/{world.runner_id}/pause", json={})
    assert paused.status_code == 200, paused.text
    assert _claimable(world) == {"default": False, "second": False}

    assert world.hub.client.post(f"/api/runners/{world.runner_id}/resume", json={}).status_code == 200
    assert _claimable(world) == {"default": True, "second": False}


def test_the_runners_read_marks_nothing_claimable_under_an_unrostered_subscription(tmp_path: Path) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("default", "ws-a")), subscription="other")

    assert _claimable(world) == {"default": False}


def test_the_runners_read_marks_nothing_claimable_for_a_runner_that_never_connected_or_is_retired(
    tmp_path: Path,
) -> None:
    world = _World(tmp_path)
    world.report(["ws-a"])
    assert _claimable(world) == {}

    world.register(bindings=_bindings(("default", "ws-a")), subscription="max")
    assert _claimable(world) == {"default": True}

    retired = world.hub.client.post(f"/api/runners/{world.runner_id}/retire", json={})
    assert retired.status_code == 200, retired.text
    assert _claimable(world) == {"default": False}
