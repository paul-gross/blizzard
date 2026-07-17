"""The review node and its fail cycle (component tier) — MVP criterion 9.

Drives the full ``build -> review -> deliver`` graph through the real hub API over a
tmp store, proving the P7 workflow-engine additions end to end at the hub seam:

* a **review node** routes ``pass -> deliver`` and ``fail -> build``;
* a review **fail** carries its ``review-findings`` **asset** artifact back into the
  build node's next envelope, latest-by-epoch;
* the fail edge's **prompt_addendum** is appended to build's re-entry prompt;
* the runner-reported **lease.minted** facts (``POST /chunks/{id}/leases``) advance the
  hub's epoch fence in lockstep, so a chunk visiting a second runner node is not
  rejected as stale — the keystone that makes a multi-runner-node graph work.

The prompts here are inline prose (POST /graphs stores them verbatim); the scripted
mock-harness end-to-end variant is the e2e tier (test_acceptance_loop is the standing
smoke; the review cycle rides the same rails).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "9"}
_ADDENDUM = "RE-ENTRY: address every review finding before declaring done."
_FINDINGS = "BLOCKING: the widget endpoint returns 500 on empty input; add a guard."

_GRAPH_YAML = f"""
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
          description: The work is complete and checks are green.
          to: review
        fail:
          description: The work is incomplete.
          to: build
          prompt_addendum: |
            Re-entry after a failed build.
  review:
    executor: runner
    prompt: |
      Review the change with cold eyes.
    session: fresh
    produces:
      - review-findings
    judgement:
      prompt: |
        Render the review verdict.
      choices:
        pass:
          description: The work passes review.
          to: deliver
        fail:
          description: Review found blocking issues.
          to: build
          prompt_addendum: |
            {_ADDENDUM}
  deliver:
    executor: hub
    mode: merge-to-main
"""


def _git_artifact(commit: str) -> dict:
    return {"name": "widget", "kind": "git_commit", "repo": "acme/widget", "branch_name": "b", "commit_hash": commit}


def _completion(node_id: str, *, epoch: int, choice: str, artifacts: list[dict]) -> dict:
    return {
        "choice": choice,
        "epoch": epoch,
        "runner_id": "r1",
        "from_node_id": node_id,
        "artifacts": artifacts,
    }


def _report_lease(hub, chunk_id: str, epoch: int) -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(f"/api/chunks/{chunk_id}/leases", json={"epoch": epoch, "runner_id": "r1"})
    assert resp.status_code == 202, resp.text


def _mint_and_claim(hub) -> tuple[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    """Mint the graph, ingest a chunk, claim it. Returns (chunk_id, node-name -> node_id)."""
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    node_ids = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}

    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    claim = hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    # The claim is pure acquisition — it does not mint the lease. The
    # runner mints its build lease and reports it up, so the hub's fence starts at 1.
    _report_lease(hub, chunk_id, epoch=1)
    return chunk_id, node_ids


def test_review_fail_carries_findings_and_addendum_back_into_build(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)

    # build (epoch 1, from the claim's lease) passes -> review.
    to_review = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], epoch=1, choice="pass", artifacts=[_git_artifact("c1")]),
    ).json()
    assert to_review["outcome"] == "next"
    assert to_review["next_envelope"]["node"]["node_name"] == "review"

    # review (epoch 2 — a fresh node-step lease the runner reports) fails, emitting the
    # findings asset. The apply-response's next envelope is build's re-entry.
    _report_lease(hub, chunk_id, epoch=2)
    to_build = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(
            nodes["review"],
            epoch=2,
            choice="fail",
            artifacts=[{"name": "review-findings", "kind": "asset", "content": _FINDINGS}],
        ),
    ).json()

    assert to_build["outcome"] == "next"
    env = to_build["next_envelope"]
    assert env["node"]["node_name"] == "build"
    # The fail edge's prompt_addendum is appended to build's base prompt.
    assert _ADDENDUM in env["prompt"]
    # The findings asset rides back into build's envelope, latest-by-epoch.
    findings = [a for a in env["artifacts"] if a["name"] == "review-findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "asset"
    assert findings[0]["content"] == _FINDINGS


def test_chunk_detail_exposes_the_review_fail_loop_and_findings_asset(tmp_path: Path) -> None:
    """The chunk detail (``GET /chunks/{id}``) surfaces the full transition history —
    including the review-fail loop back to build — and the review-findings asset content.

    This is the product surface behind MVP criterion 9/11 ("the hub's record shows every
    transition"; "every chunk's node history, artifacts … render"). Without it the
    review-fail cycle threads correctly through the envelope but is invisible to any
    reader after the fact — exactly the gap a cold verification found."""
    hub = build_hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)

    # build pass -> review, then review fail -> build (emitting the findings asset).
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], epoch=1, choice="pass", artifacts=[_git_artifact("c1")]),
    )
    _report_lease(hub, chunk_id, epoch=2)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(
            nodes["review"],
            epoch=2,
            choice="fail",
            artifacts=[{"name": "review-findings", "kind": "asset", "content": _FINDINGS}],
        ),
    )

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    # The history reads oldest-first and shows every edge, including the fail loop.
    steps = [(h["from_node_id"], h["to_node_id"], h["choice_name"]) for h in detail["history"]]
    assert steps == [
        (nodes["build"], nodes["review"], "pass"),
        (nodes["review"], nodes["build"], "fail"),
    ]
    # The last history entry agrees with the derived current node (the tail is current).
    assert detail["history"][-1]["to_node_id"] == detail["current_node_id"] == nodes["build"]
    # The edges carry the nodes' human graph names so the fail loop reads review -> build.
    named = [(h["from_node_name"], h["to_node_name"]) for h in detail["history"]]
    assert named == [("build", "review"), ("review", "build")]

    # The review-findings asset content is inline on the detail, keyed by node.name.epoch.
    findings = [a for a in detail["artifacts"] if a["name"] == "review-findings"]
    assert len(findings) == 1
    assert findings[0]["kind"] == "asset"
    assert findings[0]["content"] == _FINDINGS
    assert findings[0]["key"] == "review.review-findings.2"

    # The build's git-commit artifact carries its pinned reference, not code.
    commit = [a for a in detail["artifacts"] if a["kind"] == "git_commit"]
    assert len(commit) == 1
    assert commit[0]["repo"] == "acme/widget"
    assert commit[0]["commit_hash"] == "c1"


def test_review_cycle_second_pass_delivers_and_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)

    # First lap: build pass -> review fail -> back to build.
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], epoch=1, choice="pass", artifacts=[_git_artifact("c1")]),
    )
    _report_lease(hub, chunk_id, epoch=2)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(
            nodes["review"],
            epoch=2,
            choice="fail",
            artifacts=[{"name": "review-findings", "kind": "asset", "content": _FINDINGS}],
        ),
    )
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == nodes["build"]

    # Second lap: build pass -> review pass -> deliver (hub node) -> lands -> done.
    _report_lease(hub, chunk_id, epoch=3)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], epoch=3, choice="pass", artifacts=[_git_artifact("c2")]),
    )
    _report_lease(hub, chunk_id, epoch=4)
    delivered = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["review"], epoch=4, choice="pass", artifacts=[]),
    ).json()

    assert delivered["outcome"] == "hub_node_taken"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"
    # The deliver node landed the latest build commit through the forge.
    assert [r.commit_hash for r in hub.forge.landed] == ["c2"]


def test_review_completion_without_lease_report_is_stale(tmp_path: Path) -> None:
    """Without the runner reporting review's fresh lease, its epoch is stale.

    This is the negative that motivates the lease-report route: the hub's fence only
    advances on a recorded lease mint, so a second-node completion under an
    unreported epoch is rejected — proving the report is what keeps the two in sync.
    """
    hub = build_hub(tmp_path)
    chunk_id, nodes = _mint_and_claim(hub)
    hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["build"], epoch=1, choice="pass", artifacts=[_git_artifact("c1")]),
    )
    # Submit review at epoch 2 WITHOUT reporting the lease mint: the hub's latest epoch
    # is still 1 (the claim's), so the fence rejects epoch 2 as stale.
    stale = hub.client.post(
        f"/api/chunks/{chunk_id}/completions",
        json=_completion(nodes["review"], epoch=2, choice="pass", artifacts=[]),
    ).json()
    assert stale["outcome"] == "failure"
