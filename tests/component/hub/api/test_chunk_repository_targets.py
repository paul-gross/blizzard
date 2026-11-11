"""A chunk's repository targets (component tier): the chunk detail previews the live branch before a claim, the claim
captures it, a later link edit moves neither the captured branch nor a later claim of the same chunk, and a chunk
claimed after the edit captures the new branch. ``chunk get`` renders each target."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from blizzard.hub.cli.chunk import ChunkDetail
from tests.support.scaffolding import HubHarness, build_hub, default_project, make_ready, pointer_token

_REPOSITORY = "acme-widget"


def _ingest(hub: HubHarness, ref: str) -> str:
    pointer = {"source": "default", "ref": ref}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    return chunk_id


def _claim(hub: HubHarness, chunk_id: str) -> str:
    """Claim ``chunk_id`` for ``r1``; its route token."""
    body = {"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]}
    resp = hub.client.post("/api/fleet/routes", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["route_token"])


def _link_branch(hub: HubHarness, base_branch: str | None) -> None:
    project = default_project(hub.engine)
    resp = hub.client.patch(
        f"/api/projects/{project}/repository-links/{_REPOSITORY}", json={"base_branch": base_branch}
    )
    assert resp.status_code == 200, resp.text


def _targets(hub: HubHarness, chunk_id: str) -> list[dict[str, Any]]:
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()["repository_targets"]


def _held(targets: list[dict[str, Any]]) -> list[tuple[str, str, bool, bool]]:
    return [(t["repository_slug"], t["base_branch"], t["inherited"], t["captured_at"] is not None) for t in targets]


def test_an_unclaimed_chunk_previews_its_projects_live_branch(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub, "7")

    inherited = _held(_targets(hub, chunk_id))
    _link_branch(hub, "rust-rewrite")
    overridden = _held(_targets(hub, chunk_id))

    assert inherited == [(_REPOSITORY, "main", True, False)]
    assert overridden == [(_REPOSITORY, "rust-rewrite", False, False)]


def test_a_claim_captures_the_branch_and_a_later_edit_moves_only_chunks_claimed_after_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    early, late = _ingest(hub, "7"), _ingest(hub, "8")
    _link_branch(hub, "rust-rewrite")

    _claim(hub, early)
    captured = _targets(hub, early)
    _link_branch(hub, None)
    _claim(hub, late)

    assert _held(captured) == [(_REPOSITORY, "rust-rewrite", False, True)]
    assert _targets(hub, early) == captured, "a link edit after capture moves no captured target"
    assert _held(_targets(hub, late)) == [(_REPOSITORY, "main", True, True)]


def test_a_released_and_reclaimed_chunk_keeps_its_first_capture(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _ingest(hub, "7")
    token = _claim(hub, chunk_id)
    captured = _targets(hub, chunk_id)
    _link_branch(hub, "rust-rewrite")
    release = {"epoch": 1, "reason": "throttled", "runner_id": "r1", "route_token": token}
    released = hub.client.post(f"/api/fleet/chunks/{chunk_id}/release", json=release)
    assert released.status_code == 200, released.text

    _claim(hub, chunk_id)

    assert _targets(hub, chunk_id) == captured
    assert _held(captured) == [(_REPOSITORY, "main", True, True)]


def test_chunk_get_renders_each_target_with_its_origin_and_capture() -> None:
    body: dict[str, Any] = {
        "chunk_id": "ch_1",
        "graph_id": "g1",
        "status": "ready",
        "repository_targets": [
            {
                "repository_id": "repo_1",
                "repository_slug": "acme-widget",
                "base_branch": "rust-rewrite",
                "inherited": False,
                "captured_at": "2026-11-11T09:00:00Z",
            },
            {
                "repository_id": "repo_2",
                "repository_slug": "acme-lib",
                "base_branch": "main",
                "inherited": True,
                "captured_at": None,
            },
        ],
    }

    lines = list(ChunkDetail(body).lines())

    assert "  repository acme-widget: rust-rewrite (override, captured 2026-11-11T09:00:00Z)" in lines
    assert "  repository acme-lib: main (inherited, preview)" in lines
