"""The projects a runner serves, over the wire of a real hub process — what adding and registering store, what the
matched peek then offers, and how a claim of an unserved project's chunk is refused, read back through the hub's own
API. The resolution rules themselves are pinned at the component tier (``tests/test_runner_served_projects.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tests.e2e.test_acceptance_loop import _forge, _hub
from tests.service.support import service_gate
from tests.service.test_hub_service import _ingest, _stack

pytestmark = [pytest.mark.service, service_gate]

#: Satisfies a chunk with no declared harness preference, via its ``default`` binding.
_CAPABILITIES = [{"harness_id": "claude", "default": True}]


class _PreSliceIncompatibleDenial(BaseModel):
    """The incompatible-denial 409 body as a runner built before projects reads it — frozen here, field for field,
    so a change to the live model cannot hide a break in what an older runner parses."""

    chunk_id: str
    incompatible_runner_id: str
    detail: str = "runner capabilities no longer satisfy the chunk's reachable lineage"


def _pre_slice_reading(body: dict[str, Any]) -> str:
    """Which outcome a runner built before projects reads a claim's 409 body as: its discriminators, in its order."""
    if "status" in body:
        return "terminal"
    if "prerequisite_chunk_id" in body:
        return "dependency"
    if "incompatible_runner_id" in body:
        return "incompatible"
    return "conflict"


def _add(hub: httpx.Client, name: str, *, projects: list[str] | None = None) -> tuple[str, dict[str, str]]:
    """Add a runner, naming ``projects`` only when given, and answer its id and bearer headers."""
    body: dict[str, object] = {"name": name}
    if projects is not None:
        body["projects"] = projects
    added = hub.post("/api/runners", json=body)
    assert added.status_code == 201, added.text
    return added.json()["runner_id"], {"Authorization": f"Bearer {added.json()['token']}"}


def _register(hub: httpx.Client, bearer: dict[str, str], **declared: list[str]) -> dict[str, Any]:
    """Register as the runner ``bearer`` names; the body carries ``projects`` only when ``declared`` names it."""
    body: dict[str, object] = {"workspace_id": "w1", "capabilities": _CAPABILITIES, **declared}
    registered = hub.post("/api/fleet/runners", json=body, headers=bearer)
    assert registered.status_code == 201, registered.text
    return registered.json()


def _served_ids(views: list[dict[str, Any]]) -> list[str | None]:
    return [view["project_id"] for view in views]


def _registry_served(hub: httpx.Client, runner_id: str) -> list[dict[str, Any]]:
    listed = hub.get("/api/runners")
    assert listed.status_code == 200, listed.text
    (runner,) = [r for r in listed.json()["runners"] if r["runner_id"] == runner_id]
    return runner["projects"]


def _offered(hub: httpx.Client, bearer: dict[str, str]) -> list[str]:
    peek = hub.post("/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES}, headers=bearer)
    assert peek.status_code == 200, peek.text
    return [entry["chunk_id"] for entry in peek.json()["entries"]]


def _project(hub: httpx.Client, slug: str) -> str:
    listed = hub.get("/api/projects")
    assert listed.status_code == 200, listed.text
    (project,) = [p for p in listed.json()["projects"] if p["slug"] == slug]
    return project["project_id"]


def _create_project(hub: httpx.Client, slug: str) -> str:
    created = hub.post("/api/projects", json={"slug": slug, "name": slug.title()})
    assert created.status_code == 201, created.text
    return created.json()["project_id"]


def test_a_registration_naming_no_projects_keeps_the_declaration_and_an_empty_list_serves_nothing(
    tmp_path: Path,
) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        default = _project(hub, "default")
        chunk_id = _ingest(forge, hub, "a chunk of the served project")
        runner_id, bearer = _add(hub, "declared", projects=["default"])

        kept = _register(hub, bearer)
        assert [(v["entry"], v["project_id"], v["slug"]) for v in kept["projects"]] == [("default", default, "default")]
        assert _served_ids(_registry_served(hub, runner_id)) == [default]
        assert _offered(hub, bearer) == [chunk_id]

        cleared = _register(hub, bearer, projects=[])
        assert cleared["projects"] == []
        assert _registry_served(hub, runner_id) == []
        assert _offered(hub, bearer) == []

        # Naming no projects again keeps the empty declaration the hub now holds.
        assert _register(hub, bearer)["projects"] == []
        assert _offered(hub, bearer) == []


def test_adding_a_runner_naming_no_projects_serves_a_one_project_tenants_project_by_id(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port), _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        default = _project(hub, "default")
        runner_id, bearer = _add(hub, "sole-default")

        stored = _registry_served(hub, runner_id)
        assert [(v["entry"], v["project_id"], v["slug"]) for v in stored] == [(default, default, "default")]
        assert _served_ids(_register(hub, bearer)["projects"]) == [default]


def test_adding_a_runner_naming_no_projects_serves_nothing_on_a_two_project_tenant(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port), _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        _create_project(hub, "beta")
        runner_id, bearer = _add(hub, "no-default")

        assert _registry_served(hub, runner_id) == []
        assert _register(hub, bearer)["projects"] == []


def test_claiming_an_unserved_projects_chunk_is_a_project_denial_an_older_runner_reads_as_incompatible(
    tmp_path: Path,
) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "a chunk of the default project")
        beta = _create_project(hub, "beta")
        runner_id, bearer = _add(hub, "beta-only", projects=["beta"])
        assert _served_ids(_register(hub, bearer)["projects"]) == [beta]

        claim = hub.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "workspace_id": "w1", "environment_ids": ["env-a"]},
            headers=bearer,
        )

        assert claim.status_code == 409, claim.text
        body = claim.json()
        assert (body["chunk_id"], body["incompatible_runner_id"], body["reason"]) == (chunk_id, runner_id, "project")
        assert "default" in body["detail"]
        assert _pre_slice_reading(body) == "incompatible"
        denial = _PreSliceIncompatibleDenial.model_validate(body)
        assert (denial.chunk_id, denial.incompatible_runner_id, denial.detail) == (
            chunk_id,
            runner_id,
            body["detail"],
        )
        assert hub.get(f"/api/chunks/{chunk_id}").json()["route"] is None
