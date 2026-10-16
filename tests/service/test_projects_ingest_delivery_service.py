"""Projects at the hub's wire — the real hub against the mock forge and mock runner (service tier).

A tenant holding two projects, each drawing from its own work source: an ingest that names no project mints into
the one its sources link, and one its sources leave open is refused whole, naming the projects to choose from. A
chunk whose commit names a repository its project does not link reaches its land script's ``deliver`` step, which
is refused before the script runs — nothing reaches the forge — with a critical ``repository-not-linked`` event read
back over the events API, and the step's failure route taken. sqlite only. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import yaml

from tests.fleet_launchers import OWNER, REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME, mint_world
from tests.service.support import (
    mock_runner,
    poll_until,
    require_mock_fleet,
    service_gate,
)
from tests.support import fixture_work_source, free_port

pytestmark = [pytest.mark.service, service_gate]

#: The second project's own work source and the fixture forge repository it draws from, a copy of ``toy-api``.
_SECOND_SOURCE = "toy-cli"
_SECOND_REPO = f"{OWNER}/{_SECOND_SOURCE}"


@contextmanager
def _two_projects(tmp_path: Path) -> Iterator[tuple[httpx.Client, httpx.Client, int]]:
    """A real hub over the fixture forge whose ``default`` project links the ``toy-api`` source and repository,
    and whose ``second`` project links only :data:`_SECOND_SOURCE` — no repository. Yields the forge, the hub and
    the hub's port."""
    bin_dir = require_mock_fleet()
    _workspace, origins, bare = mint_world(bin_dir, tmp_path / "scratch")
    subprocess.run(["git", "clone", "-q", "--bare", str(bare), str(origins / f"{_SECOND_SOURCE}.git")], check=True)
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        created = hub.post("/api/projects", json={"slug": "second", "name": "Second"})
        assert created.status_code == 201, created.text
        source = fixture_work_source(_SECOND_SOURCE, _SECOND_REPO, f"http://127.0.0.1:{forge_port}")
        added = hub.post("/api/work-sources", json=source.model_dump())
        assert added.status_code == 201, added.text
        linked = hub.put(f"/api/projects/second/source-links/{_SECOND_SOURCE}")
        assert linked.status_code == 200, linked.text
        yield forge, hub, hub_port


def _issue(forge: httpx.Client, title: str, *, repo: str = REPO) -> int:
    filed = forge.post(f"/repos/{repo}/issues", json={"title": title, "body": "the chunk"})
    assert filed.status_code == 201, filed.text
    return int(filed.json()["number"])


def _chunk_ids(hub: httpx.Client) -> list[str]:
    listed = hub.get("/api/chunks")
    assert listed.status_code == 200, listed.text
    return [chunk["chunk_id"] for chunk in listed.json()["chunks"]]


def test_an_ingest_naming_no_project_mints_into_the_project_its_source_links(tmp_path: Path) -> None:
    with _two_projects(tmp_path) as (forge, hub, _port):
        first = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{_issue(forge, 'into default')}"]})
        second = hub.post(
            "/api/chunks", json={"tokens": [f"{_SECOND_SOURCE}:{_issue(forge, 'into second', repo=_SECOND_REPO)}"]}
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text

        landed = {
            name: hub.get(f"/api/chunks/{resp.json()['chunk_id']}").json()
            for name, resp in (("first", first), ("second", second))
        }
        assert (landed["first"]["project_slug"], landed["first"]["project_name"]) == ("default", "Default")
        assert (landed["second"]["project_slug"], landed["second"]["project_name"]) == ("second", "Second")
        assert landed["first"]["project_id"] != landed["second"]["project_id"]


def test_an_ingest_whose_sources_disagree_is_refused_whole_naming_the_projects(tmp_path: Path) -> None:
    with _two_projects(tmp_path) as (forge, hub, _port):
        tokens = [f"{REPO_NAME}:{_issue(forge, 'one')}", f"{_SECOND_SOURCE}:{_issue(forge, 'two', repo=_SECOND_REPO)}"]

        refused = hub.post("/api/chunks", json={"tokens": tokens})

        assert refused.status_code == 422, refused.text
        detail = refused.json()["detail"]
        assert detail["code"] == "project_required"
        assert sorted(project["slug"] for project in detail["projects"]) == ["default", "second"]
        assert _chunk_ids(hub) == []


def _not_linked_graph_yaml() -> str:
    """``build`` hands a commit to a ``deliver`` hub step running the default land script, whose ``failure`` routes
    to ``triage`` — a node nothing else reaches."""
    graph = {
        "name": "not-linked-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "judgement": {"prompt": "# judge", "choices": {"pass": {"description": "green", "to": "deliver"}}},
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed to deliver.", "to": "triage"},
                    }
                },
            },
            "triage": {
                "executor": "runner",
                "prompt": "# triage",
                "judgement": {"prompt": "# judge", "choices": {"pass": {"description": "seen", "to": "done"}}},
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


_COMMIT_ON_TOY_API = [
    {
        "name": "commit",
        "kind": "git_commit",
        "repo": REPO_NAME,
        "branch_name": "bz/not-linked",
        "commit_hash": "a" * 40,
    }
]


def test_a_commit_its_project_does_not_link_is_refused_at_deliver_before_the_land_script(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    with _two_projects(tmp_path) as (forge, hub, hub_port):
        minted = hub.post("/api/graphs", json={"definition_yaml": _not_linked_graph_yaml()})
        assert minted.status_code == 201, minted.text
        nodes = {node["name"]: node["node_id"] for node in minted.json()["nodes"]}
        ingested = hub.post(
            "/api/chunks", json={"tokens": [f"{_SECOND_SOURCE}:{_issue(forge, 'unlinked land', repo=_SECOND_REPO)}"]}
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.get(f"/api/chunks/{chunk_id}").json()["project_slug"] == "second"
        repinned = hub.patch(f"/api/chunks/{chunk_id}", json={"graph_id": minted.json()["graph_id"]})
        assert repinned.status_code == 202, repinned.text
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        added = hub.post("/api/runners", json={"name": "runner-second", "projects": ["second"]})
        assert added.status_code == 201, added.text

        with mock_runner(bin_dir, free_port(), hub_port, token=added.json()["token"]) as runner:
            assert runner.post("/_drive/register").json()["status"] == 201
            assert runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()["claimed"] is True
            completed = runner.post(
                "/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass", "artifacts": _COMMIT_ON_TOY_API}
            ).json()
            assert completed["response"]["outcome"] == "hub_node_taken", completed

            def at_triage() -> bool:
                return hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == nodes["triage"]

            assert poll_until(at_triage, timeout=30.0), hub.get(f"/api/chunks/{chunk_id}").json()

        events = hub.get("/api/events", params={"chunk_id": chunk_id})
        assert events.status_code == 200, events.text
        (refusal,) = [event for event in events.json()["events"] if event["kind"] == "repository-not-linked"]
        assert refusal["severity"] == "critical"
        assert f"repository record {REPO_NAME}" in refusal["message"]
        assert "project second" in refusal["message"]
        artifacts = [artifact["name"] for artifact in hub.get(f"/api/chunks/{chunk_id}").json()["artifacts"]]
        assert "hub-log.repository-not-linked" in artifacts
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"})
        assert pulls.status_code == 200, pulls.text
        assert pulls.json() == []
