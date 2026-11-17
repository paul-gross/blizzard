"""The fleet wire's repository targets (component tier): the matched peek names an offered chunk's targets to a runner
that prepares at them, offers a runner that does not no chunk a project link's own branch targets, and the claim
refuses environments prepared at targets that are not the chunk's (``reason`` ``targets``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.support.hub_request_auth import bearer, enroll_runner, register_runner
from tests.support.scaffolding import HubHarness, build_hub, default_project, ingest, link_project, seed_project

_REPOSITORY = "acme-widget"
_CAPABILITY = [{"harness_id": "claude", "default": True}]


def _token(hub: HubHarness, projects: list[str] | None = None) -> str:
    """A registered runner's token, its registration reporting the capabilities every default chunk needs and serving
    ``projects`` when named."""
    register_runner(hub, runner_id="runner-a", workspace_id="ws-a")
    token = enroll_runner(hub, "runner-a")
    body: dict[str, Any] = {"runner_id": "runner-a", "workspace_id": "ws-a", "capabilities": _CAPABILITY}
    if projects is not None:
        body["projects"] = projects
    resp = hub.client.post("/api/fleet/runners", json=body, headers=bearer(token))
    assert resp.status_code in (200, 201), resp.text
    return token


def _ingest(hub: HubHarness, ref: str) -> str:
    resp = hub.client.post("/api/chunks", json={"tokens": [f"default:{ref}"]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert promoted.status_code == 202, promoted.text
    return chunk_id


def _link_branch(hub: HubHarness, base_branch: str | None, project: str | None = None) -> None:
    project = project if project is not None else default_project(hub.engine)
    resp = hub.client.patch(
        f"/api/projects/{project}/repository-links/{_REPOSITORY}", json={"base_branch": base_branch}
    )
    assert resp.status_code == 200, resp.text


def _peek(hub: HubHarness, token: str, *, opt_in: bool) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"capabilities": _CAPABILITY, "repository_targets": opt_in}
    resp = hub.client.post("/api/fleet/queue/peek", json=body, headers=bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["entries"]


def _claim(hub: HubHarness, token: str, chunk_id: str, targets: list[dict[str, str]] | None) -> Any:
    body: dict[str, Any] = {
        "chunk_id": chunk_id,
        "workspace_id": "ws-a",
        "environment_ids": ["env-a"],
        "repository_targets": targets,
    }
    return hub.client.post("/api/fleet/routes", json=body, headers=bearer(token))


def _repository_id(entry: dict[str, Any]) -> str:
    return str(entry["repository_targets"][0]["repository_id"])


def test_a_peek_that_opts_in_names_the_offered_chunks_targets(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    chunk_id = _ingest(hub, "1")
    _link_branch(hub, "rust-rewrite")

    [entry] = _peek(hub, token, opt_in=True)

    assert entry["chunk_id"] == chunk_id
    [target] = entry["repository_targets"]
    assert {k: v for k, v in target.items() if k != "repository_id"} == {
        "repository_slug": _REPOSITORY,
        "owner": "acme",
        "repo": "widget",
        "base_branch": "rust-rewrite",
        "inherited": False,
    }


def test_a_peek_without_the_opt_in_is_never_offered_an_override_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    chunk_id = _ingest(hub, "1")

    inherited = _peek(hub, token, opt_in=False)
    _link_branch(hub, "rust-rewrite")
    overridden = _peek(hub, token, opt_in=False)

    assert [e["chunk_id"] for e in inherited] == [chunk_id]
    assert overridden == []


def test_a_claim_prepared_at_the_chunks_targets_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    _ingest(hub, "1")
    _link_branch(hub, "rust-rewrite")
    [entry] = _peek(hub, token, opt_in=True)

    resp = _claim(
        hub, token, entry["chunk_id"], [{"repository_id": _repository_id(entry), "base_branch": "rust-rewrite"}]
    )

    assert resp.status_code == 201, resp.text


def test_a_claim_prepared_at_stale_targets_is_refused_targets(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    _ingest(hub, "1")
    [entry] = _peek(hub, token, opt_in=True)
    _link_branch(hub, "rust-rewrite")

    resp = _claim(hub, token, entry["chunk_id"], [{"repository_id": _repository_id(entry), "base_branch": "main"}])

    assert resp.status_code == 409, resp.text
    assert resp.json()["reason"] == "targets"
    detail = hub.client.get(f"/api/chunks/{entry['chunk_id']}").json()
    assert detail["status"] == "ready", "a refused claim captures nothing and leaves the chunk claimable"
    assert [t["captured_at"] for t in detail["repository_targets"]] == [None]


def test_a_claim_naming_no_targets_is_refused_only_while_a_link_names_its_own_branch(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = _token(hub)
    overridden, inherited = _ingest(hub, "1"), _ingest(hub, "2")
    _link_branch(hub, "rust-rewrite")

    refused = _claim(hub, token, overridden, None)
    _link_branch(hub, None)
    admitted = _claim(hub, token, inherited, None)

    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == "targets"
    assert admitted.status_code == 201, admitted.text


def _hold_peek(hub: HubHarness, token: str, projects: list[str], *, opt_in: bool) -> list[dict[str, Any]]:
    body: dict[str, Any] = {
        "capabilities": _CAPABILITY,
        "repository_targets": opt_in,
        "policy": "hold",
        "projects": projects,
    }
    resp = hub.client.post("/api/fleet/queue/peek", json=body, headers=bearer(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["entries"]


def _at(entries: list[dict[str, Any]]) -> tuple[str, int] | None:
    return (entries[0]["chunk_id"], entries[0]["position"]) if entries else None


def _scenario_at(entry: dict[str, Any] | None) -> tuple[str, int] | None:
    return (entry["chunk_id"], entry["position"]) if entry else None


def test_next_claims_two_scenarios_are_the_peeks_answers_in_each_target_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    default = default_project(hub.engine)
    beta = seed_project(hub.engine, "beta")
    link_project(hub.engine, project=beta, source="default", repository=_REPOSITORY)
    token = _token(hub, [default, beta])
    blocked = ingest(hub, [{"source": "default", "ref": "1"}], project=default)
    overridden = ingest(hub, [{"source": "default", "ref": "2"}], project=default)
    inherited = ingest(hub, [{"source": "default", "ref": "3"}], project=beta)
    declared = hub.client.post(f"/api/chunks/{blocked}/dependencies", json={"prerequisite_chunk_id": inherited})
    assert declared.status_code == 202, declared.text
    _link_branch(hub, "rust-rewrite", default)

    resp = hub.client.get("/api/runners/runner-a/next-claim")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    alternative = body["without_repository_targets"]
    projects = [default, beta]

    assert _scenario_at(body["next"]) == _at(_peek(hub, token, opt_in=True)) == (overridden, 1)
    assert _scenario_at(alternative["next"]) == _at(_peek(hub, token, opt_in=False)) == (inherited, 2)
    assert _scenario_at(body["if_holding"]) == _at(_hold_peek(hub, token, projects, opt_in=True))
    assert _scenario_at(alternative["if_holding"]) == _at(_hold_peek(hub, token, projects, opt_in=False))
    assert _scenario_at(alternative["if_holding"]) == (inherited, 2), "an excluded head never stops a hold"
    assert [e["chunk_id"] for e in body["passed"]] == [blocked]
    assert (alternative["passed"], alternative["held_at"], alternative["excluded"]) == ([], [], 2)
    assert "excluded" not in body
    detail = hub.client.get(f"/api/chunks/{overridden}").json()
    assert [t["captured_at"] for t in detail["repository_targets"]] == [None], "neither route captures a target"
