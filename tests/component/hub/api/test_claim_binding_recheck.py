"""A direct claim re-judges what the matched peek would have offered (component tier): the host's flags against the
runner's bindings and subscription, and the submitted workspace against the binding for the chunk's project — read
fresh at the claim, so a host report that changes after the peek changes the answer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from blizzard.hub.store import schema as s
from tests.support.binding_world import BindingWorld, bindings_of
from tests.support.hub_request_auth import bearer
from tests.support.scaffolding import hub_scoped


def _register(world: BindingWorld, **body: Any) -> None:
    world.register(capabilities=[{"harness_id": "claude", "default": True}], **body)


def _claim(world: BindingWorld, slug: str, workspace_id: str) -> Any:
    return world.hub.client.post(
        "/api/fleet/routes",
        json={
            "chunk_id": world.chunks[slug],
            "runner_id": world.runner_id,
            "workspace_id": workspace_id,
            "environment_ids": ["env-a"],
        },
        headers=bearer(world.token),
    )


def _recorded(world: BindingWorld, slug: str) -> tuple[int, int, int]:
    """The chunk's route, route-token and epoch-owner rows — what a won claim writes."""
    chunk_id = world.chunks[slug]
    with world.hub.engine.connect() as conn:

        def count(table: Any) -> int:
            query = select(func.count()).select_from(table).where(table.c.chunk_id == chunk_id)
            return int(conn.execute(hub_scoped(query)).scalar_one())

        return count(s.route_created), count(s.route_token_minted), count(s.epoch_owners)


def _route(world: BindingWorld, slug: str) -> Any:
    return world.hub.client.get(f"/api/chunks/{world.chunks[slug]}").json()["route"]


def test_a_claim_through_a_workspace_the_host_has_not_reported_is_refused_until_it_reports_it(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=bindings_of(("default", "ws-a"), ("second", "ws-b")), subscription="max")

    before = _recorded(world, "second")
    refused = _claim(world, "second", "ws-b")

    assert refused.status_code == 409, refused.text
    assert _recorded(world, "second") == before  # no route, no minted token, no epoch
    assert refused.json()["reason"] == "binding"
    assert refused.json()["incompatible_runner_id"] == world.runner_id
    assert _route(world, "second") is None
    assert world.hub.client.get(f"/api/chunks/{world.chunks['second']}").json()["status"] == "ready"

    world.report(["ws-a", "ws-b"])  # no re-registration

    assert _claim(world, "second", "ws-b").status_code == 201


def test_a_claim_through_another_workspace_than_the_projects_binding_is_refused(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a", "ws-b"])
    _register(world, bindings=bindings_of(("default", "ws-a"), ("second", "ws-b")), subscription="max")

    before = _recorded(world, "default")
    wrong = _claim(world, "default", "ws-b")

    assert (wrong.status_code, wrong.json()["reason"]) == (409, "binding")
    assert _recorded(world, "default") == before
    assert _route(world, "default") is None
    assert _claim(world, "default", "ws-a").status_code == 201


def test_a_claim_under_an_unrostered_subscription_is_refused_with_or_withoutbindings_of(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=bindings_of(("default", "ws-a")), subscription="other")
    with_bindings = _claim(world, "default", "ws-a")
    _register(world, projects=["default"], subscription="other")
    without = _claim(world, "default", "ws-a")

    assert [(r.status_code, r.json()["reason"]) for r in (with_bindings, without)] == [(409, "binding")] * 2


def test_a_legacy_registration_is_judged_by_its_served_declaration_alone(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, projects=["default"])

    assert _claim(world, "default", "any-workspace").status_code == 201


def test_a_re_registration_with_other_bindings_changes_the_answer(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a", "ws-b"])
    _register(world, bindings=bindings_of(("default", "ws-a")), subscription="max")
    assert _claim(world, "default", "ws-b").status_code == 409

    _register(world, bindings=bindings_of(("default", "ws-b")), subscription="max")

    assert _claim(world, "default", "ws-b").status_code == 201


def test_an_unserved_project_still_answers_project_not_binding(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=bindings_of(("default", "ws-a")), subscription="max")

    refused = _claim(world, "second", "ws-z")

    assert (refused.status_code, refused.json()["reason"]) == (409, "project")


def test_a_held_route_keeps_its_workspace_when_the_bindings_later_change(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=bindings_of(("default", "ws-a")), subscription="max")
    assert _claim(world, "default", "ws-a").status_code == 201

    world.report([])

    assert _route(world, "default")["workspace_id"] == "ws-a"


def test_an_empty_bindings_list_admits_no_claim_where_absent_bindings_do(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=[], projects=["default"], subscription="max")

    refused = _claim(world, "default", "ws-a")

    assert (refused.status_code, refused.json()["reason"]) == (409, "project")


def test_a_hostless_runner_with_bindings_still_needs_the_bound_workspace(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path, hosted=False)
    _register(world, bindings=bindings_of(("default", "ws-a")))

    wrong = _claim(world, "default", "ws-z")
    right = _claim(world, "default", "ws-a")

    assert [(wrong.status_code, wrong.json().get("reason")), right.status_code] == [(409, "binding"), 201]


def test_a_host_that_never_described_itself_flags_nothing_but_the_workspace_must_match(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)  # hosted, never reported
    _register(world, bindings=bindings_of(("default", "ws-a")), subscription="nope")

    wrong = _claim(world, "default", "ws-z")
    right = _claim(world, "default", "ws-a")

    assert [(wrong.status_code, wrong.json().get("reason")), right.status_code] == [(409, "binding"), 201]


def test_with_duplicate_aliases_one_valid_matching_binding_is_enough(tmp_path: Path) -> None:
    world = BindingWorld(tmp_path)
    world.report(["ws-a"])
    _register(world, bindings=bindings_of(("default", "ws-x"), ("default", "ws-a")), subscription="max")

    assert _claim(world, "default", "ws-a").status_code == 201
