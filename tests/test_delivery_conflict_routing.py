"""#241 AC2/AC3: the SHIPPED ``advanced-development-workflow`` graph's ``deliver`` node
actually routes a printed ``conflict`` outcome — component tier.

Mints the real, packaged graph, seeding the chunk directly at ``deliver``'s minted node
id via a direct transition-fact insert.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import DEFAULT_BOUNCE_CAP
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.graphs.scripts import land_pr_ci
from tests.support import (
    DEFAULT_FIXTURE_REPOSITORIES,
    FakeHubCommandRunner,
    FakeHubWorkdir,
    HubHarness,
    build_hub,
    chunk_facts_of,
    make_ready,
    pointer_token,
    report_lease,
)

pytestmark = pytest.mark.component

_LAND_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"


def _writable_movement(hub: HubHarness) -> IWriteChunkMovementRepository:
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def _writable_escalations(hub: HubHarness) -> IWriteChunkEscalationsRepository:
    return cast(IWriteChunkEscalationsRepository, hub.services.chunks.escalations)


def _mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk (which pins to the hub's own
    packaged **default** graph — ingest names no graph), then repin it onto adv-dwf via
    ``PATCH /chunks/{id}`` (legal while the chunk is still ``not_ready``) before claiming
    a route, so every node id resolved off the mint response is the one the claimed
    chunk's pin actually recognizes."""
    definition_yaml = PACKAGED.named("advanced-development-workflow").inlined_yaml
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert repin.status_code == 202, repin.text
    make_ready(hub, chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver_with_an_unlanded_commit(
    hub: HubHarness, chunk_id: str, nodes: dict[str, str], *, repos: tuple[str, ...] = ("acme/widget",)
) -> None:
    """Place the chunk's current node directly at ``deliver`` (standing in for the six
    node-steps a real chunk would take to arrive here — same technique
    ``tests/test_delivery_incomplete_routing.py`` uses for ``retrospective``), carrying
    one ``git_commit`` artifact for a repo with no ``merged/<repo>`` marker, so this
    reads as a genuine, unlanded delivery attempt."""
    commit_artifacts = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name=repo.split("/")[1],
            data=f"feat/thing:{'c' * 40}",
            repo=repo,
            forge=None,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, hub.clock).value,
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for repo in repos
    ]
    _writable_movement(hub).record_transition(
        transition_id="tr_seed_deliver",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=commit_artifacts,
        proposals=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def test_a_dirty_conflict_routes_to_resolve_and_records_a_bounce(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=0, stdout=f"doing stuff\n{land_pr_ci._CONFLICT}\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True
    assert body["outcome_choice"] == "conflict"

    # The accepted transition's target node is `resolve` — the named assertion a
    # mutation proof (deleting the choice from graph.yaml) must break.
    assert body["to_node_name"] == "resolve"

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_id"] == nodes["resolve"]
    assert detail["current_node_name"] == "resolve"

    assert len(detail["bounces"]) == 1
    assert detail["bounces"][0]["cause"] == "conflict"
    bounce_assets = [a for a in detail["artifacts"] if a["name"] == "bounce-envelope"]
    assert len(bounce_assets) == 1, detail["artifacts"]
    assert detail["landed"] is False

    # The negative that is the whole point of this test: no unroutable-outcome
    # artifact, no unroutable-outcome event.
    unroutable_artifacts = [a for a in detail["artifacts"] if a["name"] == "hub-unroutable-outcome"]
    assert unroutable_artifacts == []
    unroutable_events = [
        e for e in hub.services.chunks.events.list_events(chunk_id=chunk_id) if e.kind == "hub-node-unroutable-outcome"
    ]
    assert unroutable_events == []


def test_a_dirty_conflict_escalates_once_the_bounce_cap_is_crossed(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=0, stdout=f"{land_pr_ci._CONFLICT}\n", stderr=""))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)

    # `deliver` falls back to `DEFAULT_BOUNCE_CAP` (5) — pre-seed that many prior bounces
    # (idempotent per `(chunk_id, epoch)`) so this run's kick-back crosses it.
    for epoch in range(1, DEFAULT_BOUNCE_CAP + 1):
        _writable_escalations(hub).record_bounce(
            chunk_id, epoch=epoch, cause="conflict", envelope="{}", at=hub.clock.now()
        )
    report_lease(hub, chunk_id, epoch=DEFAULT_BOUNCE_CAP + 1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert len(detail["bounces"]) == DEFAULT_BOUNCE_CAP + 1
    assert detail["escalation"]["cause"] == "bounce-cap"
    assert detail["escalation"]["detail"] == (
        f"bounce cap ({DEFAULT_BOUNCE_CAP}) crossed after {DEFAULT_BOUNCE_CAP + 1} bounces"
    )
    # The escalation holds the route: the runner's tenure outlives it, so a requeue resumes in place.
    assert chunk_facts_of(hub, chunk_id).routes.newest is not None


def _envelope(hub: HubHarness, chunk_id: str) -> dict:
    (bounce,) = hub.client.get(f"/api/chunks/{chunk_id}").json()["bounces"]
    return json.loads(bounce["envelope"])


def test_a_failed_deliver_step_envelope_names_the_unlanded_repo_and_the_step_output(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=1, stdout="", stderr="boom — merge refused\n" + "x" * 5000))
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir())
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)
    assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance").json()["ran"] is True

    envelope = _envelope(hub, chunk_id)
    assert [u["repo"] for u in envelope["unlanded"]] == ["acme/widget"]
    assert envelope["unlanded"][0]["branch"] == "feat/thing"
    assert envelope["unlanded"][0]["commit"] == "c" * 40
    assert envelope["unlanded"][0]["pr"] is None  # a repo with no PR
    assert envelope["step"]["exit_code"] == 1
    assert len(envelope["step"]["output_tail"]) == 2000
    assert envelope["step"]["log_artifact"] == f"hub-log.{envelope['step']['name']}"
    assert "acme/widget" in envelope["detail"] and envelope["step"]["name"] in envelope["detail"]


def test_an_envelope_lists_only_the_unlanded_repo_with_its_pr_and_keeps_non_ascii_unescaped(tmp_path: Path) -> None:
    runner = FakeHubCommandRunner()
    runner.arm(_LAND_COMMAND, CommandResult(exit_code=1, stdout="", stderr="émoji ✗"))
    repositories = [replace(DEFAULT_FIXTURE_REPOSITORIES[0], repo=name) for name in ("widget", "done")]
    hub = build_hub(tmp_path, hub_command_runner=runner, hub_workdir=FakeHubWorkdir(), repositories=repositories)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_an_unlanded_commit(hub, chunk_id, nodes, repos=("acme/done", "acme/widget"))
    artifacts = cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
    for name, data in (
        ("merged/acme/done", "d" * 40),
        (
            "delivery-pr/acme/widget",
            json.dumps({"repo": "acme/widget", "number": 945, "url": "https://x/acme/widget/pull/945"}),
        ),
    ):
        artifacts.record_hub_artifact(
            chunk_id,
            node_id=nodes["deliver"],
            node_name="deliver",
            epoch=1,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=name,
            content=data,
            at=hub.clock.now(),
        )
    report_lease(hub, chunk_id, epoch=1, seq=1)
    hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")

    envelope = _envelope(hub, chunk_id)
    assert [u["repo"] for u in envelope["unlanded"]] == ["acme/widget"]
    assert envelope["unlanded"][0]["pr"] == {"number": 945, "url": "https://x/acme/widget/pull/945"}
    assert envelope["detail"].startswith("PR #945 in acme/widget did not land: `")
    assert "émoji ✗" in envelope["step"]["output_tail"]
    (bounce,) = hub.client.get(f"/api/chunks/{chunk_id}").json()["bounces"]
    assert "\\u" not in bounce["envelope"]
