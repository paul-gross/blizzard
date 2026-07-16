"""Open-pr delivery mode over the hub boundary (component tier).

The counterpart to ``test_delivery_loop`` for the ``open-pr`` deliver mode (D-059/D-065):
a chunk travels ingest -> claim -> completion -> deliver, and instead of merging, the
coordinator opens a PR and **parks** the chunk — ``delivering`` with the awaiting-external-
merge detail and its environments still held (D-066). A later ``POST /check-delivery``
detects the external merge and drives the chunk to ``done``, releasing the route.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import FakeForge, build_hub, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "12"}

_OPEN_PR_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: deliver
        fail:
          description: Incomplete.
          to: build
  deliver:
    executor: hub
    mode: open-pr
"""


def _ingest(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _OPEN_PR_YAML}).status_code == 201
    resp = hub.client.post("/api/chunks", json={"pointers": [_POINTER]})
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _claim(hub, chunk_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    report_lease(hub, chunk_id, epoch=1, seq=1)
    return resp.json()


def _build_completion(build_node_id: str, epoch: int) -> dict:
    return {
        "choice": "pass",
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": build_node_id,
        "check_results": [{"command": "mise run test", "passed": True}],
        "artifacts": [
            {
                "name": "work",
                "kind": "git_commit",
                "repo": "acme/widget",
                "branch_name": "blizzard/ch-12",
                "commit_hash": "abc123",
            }
        ],
    }


def _deliver(hub, chunk_id: str) -> str:  # type: ignore[no-untyped-def]
    build_node_id = _claim(hub, chunk_id)["envelope"]["node"]["node_id"]
    apply = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(build_node_id, 1))
    assert apply.status_code == 200, apply.text
    assert apply.json()["outcome"] == "hub_node_taken"
    return build_node_id


def test_open_pr_mode_opens_a_pr_and_parks_the_chunk(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    _deliver(hub, chunk_id)

    # A PR was opened (not merged) targeting the default base branch, and the chunk is
    # parked: delivering, awaiting an external merge, with its environments still held.
    assert forge.landed == []
    assert [(r.repo, r.branch_name, r.base_branch) for r in forge.opened] == [("acme/widget", "blizzard/ch-12", "main")]
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "delivering"
    assert detail["awaiting_external_merge"] is True
    assert [(p["repo"], p["number"]) for p in detail["open_prs"]] == [("acme/widget", 1)]
    assert detail["route"] is not None  # environments held until the terminal outcome (D-066)


def test_open_pr_targets_the_configured_base_branch(tmp_path: Path) -> None:
    # A real repo whose default branch is ``master`` (the dogfood case) sets BZ_FORGE_BASE_BRANCH,
    # threaded to the coordinator so the PR's base resolves instead of 422-ing on ``main``.
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge, base_branch="master")
    chunk_id = _ingest(hub)
    _deliver(hub, chunk_id)
    assert forge.opened[0].base_branch == "master"


def test_redelivery_does_not_open_a_duplicate_pr(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    build_node_id = _deliver(hub, chunk_id)

    # A replayed completion re-enters the deliver node; the coordinator skips the repo that
    # already has a pr.opened fact (reconciliation), so no second PR is opened.
    replay = hub.client.post(f"/api/chunks/{chunk_id}/completions", json=_build_completion(build_node_id, 1))
    assert replay.json()["outcome"] == "hub_node_taken"
    assert len(forge.opened) == 1


def test_check_delivery_is_a_noop_while_the_pr_is_open(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    _deliver(hub, chunk_id)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/check-delivery")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["finalized"] is False
    assert body["open_prs"] == 1
    assert body["status"] == "delivering"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "delivering"


def test_check_delivery_finalizes_after_an_external_merge(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    _deliver(hub, chunk_id)

    # A human merges the PR on the forge; the on-demand check detects it and finalizes.
    forge.mark_merged("acme/widget", 1, landed_commit="landed-abc123")
    resp = hub.client.post(f"/api/chunks/{chunk_id}/check-delivery")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["finalized"] is True
    assert body["status"] == "done"

    final = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert final["status"] == "done"
    assert final["awaiting_external_merge"] is False
    assert final["route"] is None  # environments released on the terminal outcome (D-066)

    # A second check is idempotent — the delivery is already finalized.
    again = hub.client.post(f"/api/chunks/{chunk_id}/check-delivery")
    assert again.json()["finalized"] is False
    assert again.json()["status"] == "done"


def test_check_delivery_finalizes_a_close_without_merge(tmp_path: Path) -> None:
    forge = FakeForge()
    hub = build_hub(tmp_path, forge=forge)
    chunk_id = _ingest(hub)
    _deliver(hub, chunk_id)

    # A PR closed without merging is also terminal (D-065): the chunk moves to done.
    forge.mark_closed("acme/widget", 1)
    resp = hub.client.post(f"/api/chunks/{chunk_id}/check-delivery")
    assert resp.json()["finalized"] is True
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
