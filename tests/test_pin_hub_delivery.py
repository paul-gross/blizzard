"""Pinning tests for hub-delivery decisions that were previously defended only by prose
(``bzh:mutation-review-selection``).

Each test here converts one comment-defended decision into an assertion that fails if the
decision is reverted. Grouped by the module whose decision they pin.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.actions.visit import UnconvergedDeliveryError
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.graphs import PACKAGED
from tests.github_double import GitHubDouble
from tests.support import HubHarness, build_hub, pointer_token, report_lease

# -- hub_node: an unconverged delivery routes the failure edge ------------------------


def _writable(hub: HubHarness) -> IWriteChunkMovementRepository:
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def _mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk, repin it onto adv-dwf, and claim a
    route — the same sequence ``tests/test_delivery_conflict_routing.py`` uses."""
    definition_yaml = PACKAGED.named("advanced-development-workflow").inlined_yaml(YAML_CODEC)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    graph_id = minted.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in minted.json()["nodes"]}
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    repin = hub.client.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph_id})
    assert repin.status_code == 202, repin.text
    hub.promote(chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver_with_two_branches_for_one_repo(hub: HubHarness, chunk_id: str, nodes: dict[str, str]) -> None:
    """Place the chunk at ``deliver`` carrying two ``git_commit`` artifacts naming ONE repo
    on different branches at the same epoch — a chunk that worked the repo across two
    environments and never converged."""
    rows = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name="w",
            data=f"{branch}:{commit * 40}",
            repo="acme/widget",
            forge=None,
            artifact_id=Id.mint(IdPrefix.ARTIFACT, hub.clock).value,
            chunk_id=chunk_id,
            node_id=nodes["build"],
            node_name="build",
            epoch=1,
        )
        for branch, commit in (("feat/from-e1", "a"), ("feat/from-e2", "b"))
    ]
    _writable(hub).record_transition(
        transition_id="tr_seed_deliver",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=rows,
        admission=EpochAdmission.AT_OR_ABOVE,
    )


@pytest.mark.component
def test_an_unconverged_delivery_routes_the_failure_edge_instead_of_escaping_the_tick(tmp_path: Path) -> None:
    """A defect in what reached the node, not a fault in running it: the executor routes
    `deliver`'s authored `failure` edge and records the reason as an artifact, rather than
    letting the error escape and re-crash the tick on every poll."""
    double = GitHubDouble()
    hub = build_hub(tmp_path, forges=double.forges)
    chunk_id, nodes = _mint_and_claim(hub)
    _seed_at_deliver_with_two_branches_for_one_repo(hub, chunk_id, nodes)
    report_lease(hub, chunk_id, epoch=1, seq=1)

    try:
        outcome = hub.turn_outcome(chunk_id)
    except UnconvergedDeliveryError as exc:
        pytest.fail(f"an unconverged set must route `failure`, not escape the tick: {exc}")

    assert outcome == "failure"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_name"] == "resolve"
    assert double.calls == [], "land must never reach the forge for an unconverged set"
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert any("unconverged-delivery" in a["name"] for a in detail["artifacts"]), detail["artifacts"]
