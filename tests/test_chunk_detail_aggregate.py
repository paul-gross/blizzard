"""The chunk detail carries the whole aggregate the board renders — component tier.

``GET /chunks/{id}`` must carry the derived status, route, work refs, full transition
history, inline artifact store, open gate decision, and any escalation. Drives a
build->gate scenario and asserts every piece is present."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import ChunkVerb, WorkRef, decide_close_enqueue, verb_legal_from
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from tests.support import (
    FakeWorkSource,
    HubHarness,
    RepositorySeed,
    build_hub,
    create_repositories,
    fixture_repository,
    ingest,
    make_ready,
    pointer_token,
    report_lease,
)

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

_GATE_YAML = """
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
          to: approve-gate
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it.
          to: deliver
        reject:
          description: Send it back.
          to: build
  deliver:
    executor: hub
    action: pass
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""

_BUILD_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "feature/widget",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


def _pass_build(
    hub: HubHarness, artifacts: list[dict[str, str]], pointer: dict[str, str] = _POINTER
) -> tuple[str, dict[str, str]]:
    """Ingest ``pointer`` onto the gate graph and pass its build node producing ``artifacts``;
    the chunk's id and its graph's node ids by name."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(pointer)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)

    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1", "e2"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    completed = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": nodes["build"], "artifacts": artifacts},
    )
    assert completed.status_code == 200, completed.text
    return chunk_id, nodes


def _branch_urls(hub: HubHarness, chunk_id: str) -> dict[str, str | None]:
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    return {art["name"]: art["branch_url"] for art in detail["artifacts"] if art["kind"] == "git_commit"}


def test_detail_carries_the_full_aggregate(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _pass_build(hub, [_BUILD_ARTIFACT])

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    # Derived status + fence.
    assert detail["status"] == "waiting_on_human"
    assert detail["latest_epoch"] == 1

    # Route (runner/workspace/envs) and work refs.
    assert detail["route"]["runner_id"] == "r1"
    assert detail["route"]["environment_ids"] == ["e1", "e2"]
    assert [p["ref"] for p in detail["work_refs"]] == [_POINTER["ref"]]

    # Board-legible identity: the current node's human name and the
    # pointer's `{source}#{number}` label are resolved server-side onto the detail.
    assert detail["current_node_id"] == nodes["approve-gate"]
    assert detail["current_node_name"] == "approve-gate"
    assert detail["work_refs"][0]["label"] == "default#7"

    # Full transition history with the judgement choice on the edge, and the
    # nodes' human graph names resolved onto each edge so the timeline reads build -> gate.
    assert len(detail["history"]) == 1
    step = detail["history"][0]
    assert step["from_node_id"] == nodes["build"]
    assert step["from_node_name"] == "build"
    assert step["to_node_id"] == nodes["approve-gate"]
    assert step["to_node_name"] == "approve-gate"
    assert step["choice_name"] == "pass"

    # Inline artifact store — the git-commit reference (the hub stores the pointer),
    # with the branch link on the forge of the repository record the commit resolves to.
    assert len(detail["artifacts"]) == 1
    art = detail["artifacts"][0]
    assert art["kind"] == "git_commit"
    assert (art["repo"], art["branch_name"], art["commit_hash"]) == (
        "acme/widget",
        "feature/widget",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert art["branch_url"] == "http://forge.fixture/acme/widget/tree/feature/widget"

    # The open gate decision with its choices.
    assert detail["decision"]["node_name"] == "approve-gate"
    assert {c["name"] for c in detail["decision"]["choices"]} == {"approve", "reject"}


def test_detail_artifact_production_time_is_the_stored_marker_clock(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id, nodes = _pass_build(hub, [_BUILD_ARTIFACT])
    produced_at = hub.clock.now() - timedelta(hours=2)
    writer = cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
    assert writer.record_hub_artifact(
        chunk_id,
        node_id=nodes["approve-gate"],
        node_name="approve-gate",
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        name="delivery-pr/acme/widget/1",
        content='{"repo":"acme/widget","number":1,"url":"http://forge/acme/widget/pull/1"}',
        at=produced_at,
        close_decision=decide_close_enqueue([WorkRef(source="default", ref="7")], lambda _: True),
    )

    response = hub.client.get(f"/api/chunks/{chunk_id}")
    assert response.status_code == 200, response.text
    artifacts = {item["name"]: item for item in response.json()["artifacts"]}
    marker = artifacts["delivery-pr/acme/widget/1"]
    assert datetime.fromisoformat(marker["produced_at"]) == produced_at.astimezone(UTC)
    assert datetime.fromisoformat(marker["recorded_at"]) - produced_at > timedelta(hours=1)
    stored = {item.name: item for item in hub.services.chunks.artifacts.load_artifacts(chunk_id)}
    commit = artifacts["acme/widget"]
    assert datetime.fromisoformat(commit["produced_at"]) == stored["acme/widget"].produced_at


def test_a_tracker_sourced_chunk_links_its_branch_through_the_repository_its_commit_lands_in(
    tmp_path: Path,
) -> None:
    """The work ref's source is a tracker on no forge of the commit's; the link is the linked
    repository record's own forge, its web origin derived from the record's API base."""
    hub = build_hub(
        tmp_path,
        work_sources={"tracker": FakeWorkSource(name="tracker", repo="corp/tracker")},
        repositories=[
            RepositorySeed(
                forge_api_url="https://ghe.example/api/v3",
                owner="acme",
                repo="widget",
                base_branch="main",
                secret_name="fixture-forge-token",
            )
        ],
    )
    chunk_id, _ = _pass_build(hub, [_BUILD_ARTIFACT], pointer={"source": "tracker", "ref": "PROJ-7"})

    assert _branch_urls(hub, chunk_id) == {"acme/widget": "https://ghe.example/acme/widget/tree/feature/widget"}


def test_a_repository_on_an_unhandled_forge_has_no_github_shaped_branch_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id, _ = _pass_build(hub, [_BUILD_ARTIFACT])
    assert _branch_urls(hub, chunk_id) == {"acme/widget": "http://forge.fixture/acme/widget/tree/feature/widget"}
    forges = hub.services.forge_records
    get_many = forges.get_many

    def other_kind(ids):  # type: ignore[no-untyped-def]
        return {key: replace(forge, kind="other") for key, forge in get_many(ids).items()}

    monkeypatch.setattr(forges, "get_many", other_kind)
    assert _branch_urls(hub, chunk_id) == {"acme/widget": None}


def test_a_commit_in_a_repository_its_project_does_not_link_or_no_record_names_has_no_branch_link(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    create_repositories(hub.client, [fixture_repository("gadget", "http://forge.fixture", owner="acme")], project=None)
    commits = [
        _BUILD_ARTIFACT,
        {**_BUILD_ARTIFACT, "name": "acme/gadget", "repo": "acme/gadget"},
        {**_BUILD_ARTIFACT, "name": "acme/unknown", "repo": "acme/unknown"},
    ]
    chunk_id, _ = _pass_build(hub, commits)

    assert _branch_urls(hub, chunk_id) == {
        "acme/widget": "http://forge.fixture/acme/widget/tree/feature/widget",
        "acme/gadget": None,
        "acme/unknown": None,
    }


def test_detail_carries_the_pinned_graphs_name_and_created_at(tmp_path: Path) -> None:
    """``ChunkDetail`` carries the pinned graph's name and mint instant;
    asserts it matches what `GET /api/graphs` independently reports."""
    hub = build_hub(tmp_path)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML}).json()
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    summary = next(g for g in hub.client.get("/api/graphs").json() if g["graph_id"] == graph["graph_id"])

    assert detail["graph_id"] == graph["graph_id"]
    assert detail["graph_name"] == "default-delivery"
    assert detail["graph_name"] == summary["name"]
    assert detail["graph_created_at"] == summary["created_at"]


def _actions(detail: dict[str, object]) -> dict[str, object]:
    keys = (
        "pausable",
        "completable",
        "deletable",
        "delete_refusal",
        "promotable",
        "graph_editable",
        "terminal",
        "current_node_terminal",
    )
    return {k: detail[k] for k in keys}


def test_detail_classifies_an_unclaimed_chunk_as_deletable_and_live(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    assert _actions(detail) == {
        "pausable": True,
        "completable": True,
        "deletable": True,
        "delete_refusal": None,
        "promotable": True,
        "graph_editable": True,
        "terminal": False,
        "current_node_terminal": False,
    }


def test_detail_classifies_a_claimed_chunk_as_no_longer_deletable(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    assert detail["deletable"] is False
    assert detail["delete_refusal"] == "status"
    assert detail["promotable"] is False
    assert detail["graph_editable"] is False
    assert (detail["pausable"], detail["completable"], detail["terminal"]) == (True, True, False)


def test_detail_deletable_reads_the_delete_verb_legality_at_every_derived_status(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    seen: dict[ChunkStatus, bool] = {}

    def new_chunk(ref: str) -> str:
        token = pointer_token({"source": "default", "ref": ref})
        return hub.client.post("/api/chunks", json={"tokens": [token]}).json()["chunk_id"]

    def observe(chunk_id: str) -> None:
        detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
        status = ChunkStatus(detail["status"])
        assert detail["deletable"] is verb_legal_from(ChunkVerb.DELETE, status)
        seen[status] = detail["deletable"]

    not_ready = new_chunk("1")
    observe(not_ready)

    ready = new_chunk("2")
    make_ready(hub, ready)
    observe(ready)

    claimed = new_chunk("3")
    make_ready(hub, claimed)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": claimed, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    report_lease(hub, claimed, epoch=1, seq=1)
    observe(claimed)

    done = new_chunk("4")
    hub.client.post(f"/api/chunks/{done}/complete", json={"by": "operator"})
    observe(done)

    assert seen[ChunkStatus.NOT_READY] is True
    assert seen[ChunkStatus.DONE] is False
    assert len(seen) >= 3


def test_detail_classifies_an_operator_completed_chunk_as_terminal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    resp = hub.client.post(f"/api/chunks/{chunk_id}/complete", json={"by": "operator"})
    assert resp.status_code < 300, resp.text

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    assert detail["status"] == "done"
    assert _actions(detail) == {
        "pausable": False,
        "completable": False,
        "deletable": False,
        "delete_refusal": "status",
        "promotable": False,
        "graph_editable": False,
        "terminal": True,
        "current_node_terminal": False,
    }


def _new_chunk(hub: HubHarness, ref: str) -> str:
    token = pointer_token({"source": "default", "ref": ref})
    return hub.client.post("/api/chunks", json={"tokens": [token]}).json()["chunk_id"]


def test_detail_promotable_reads_true_for_a_paused_never_promoted_chunk_and_false_once_promoted(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    chunk_id = _new_chunk(hub, "1")
    assert hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"}).status_code == 202

    paused = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert (paused["status"], paused["promotable"]) == ("paused", True)

    promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
    assert promoted.status_code == 202, promoted.text
    assert promoted.json()["promotable"] is False
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["promotable"] is False


def test_detail_delete_refusal_names_dependents_and_agrees_with_the_delete_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    prerequisite = _new_chunk(hub, "1")
    dependent = _new_chunk(hub, "2")
    declared = hub.client.post(f"/api/chunks/{dependent}/dependencies", json={"prerequisite_chunk_id": prerequisite})
    assert declared.status_code < 300, declared.text

    detail = hub.client.get(f"/api/chunks/{prerequisite}").json()
    assert detail["delete_refusal"] == "dependents"
    assert detail["deletable"] is True
    assert [n["chunk_id"] for n in detail["neighborhood"]["dependents"]] == [dependent]
    refused = hub.client.request("DELETE", f"/api/chunks/{prerequisite}", json={})
    assert refused.status_code == 409
    assert dependent in refused.json()["detail"]

    assert hub.client.get(f"/api/chunks/{dependent}").json()["delete_refusal"] is None
    released = hub.client.post(
        f"/api/chunks/{dependent}/dependencies/release", json={"prerequisite_chunk_id": prerequisite}
    )
    assert released.status_code < 300, released.text
    assert hub.client.get(f"/api/chunks/{prerequisite}").json()["delete_refusal"] is None
    assert hub.client.request("DELETE", f"/api/chunks/{prerequisite}", json={}).status_code == 202


def test_detail_delete_refusal_puts_the_status_before_standing_dependents(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML})
    prerequisite = _new_chunk(hub, "1")
    dependent = _new_chunk(hub, "2")
    hub.client.post(f"/api/chunks/{dependent}/dependencies", json={"prerequisite_chunk_id": prerequisite})
    assert hub.client.post(f"/api/chunks/{prerequisite}/pause", json={"by": "operator"}).status_code == 202

    assert hub.client.get(f"/api/chunks/{prerequisite}").json()["delete_refusal"] == "status"
    refused = hub.client.request("DELETE", f"/api/chunks/{prerequisite}", json={})
    assert refused.status_code == 409
    assert "deletion needs a chunk at" in refused.json()["detail"]


_ONE_STEP_YAML = """
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
          to: done
        fail:
          description: Incomplete.
          to: build
"""


def test_detail_marks_a_chunk_routed_to_the_reserved_terminal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _ONE_STEP_YAML})
    build = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    make_ready(hub, chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build, "artifacts": []},
    )

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()

    assert detail["current_node_terminal"] is True
    assert detail["terminal"] is True


def test_detail_lists_every_epoch_of_a_series_that_spans_node_ids(tmp_path: Path) -> None:
    """A republication re-mints a node under a new node id but the same name, so its artifact series continues by
    node name — and the superseded entries are history, not dropped: the detail lists every one, in epoch order."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    writer = cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
    for node_id, epoch in (("nd_build_a", 1), ("nd_build_c", 3), ("nd_build_b", 2)):
        writer.record_hub_artifact(
            chunk_id,
            node_id=node_id,
            node_name="build",
            epoch=epoch,
            name="findings",
            content=f"v{epoch}",
            at=hub.clock.now(),
            admission=EpochAdmission.AT_OR_ABOVE,
            close_decision=decide_close_enqueue([WorkRef("default", "1")], lambda _: True),
        )

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert [(a["key"], a["node_id"]) for a in detail["artifacts"]] == [
        ("build.findings.1", "nd_build_a"),
        ("build.findings.2", "nd_build_b"),
        ("build.findings.3", "nd_build_c"),
    ]
