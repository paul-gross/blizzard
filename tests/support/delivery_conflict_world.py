"""Helpers for the delivery conflict routing tests, shared with other test modules."""

from __future__ import annotations

from typing import TypedDict, Unpack, cast

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.forge.landing import Check, Mergeability
from blizzard.hub.graphs import PACKAGED
from tests.support.github_double import GitHubDouble, forge_holding
from tests.support.scaffolding import (
    HubHarness,
    make_ready,
    pointer_token,
)

_COMMIT = "c" * 40


class _RepoState(TypedDict, total=False):
    mergeability: Mergeability
    checks: dict[str, tuple[Check, ...] | None]


def github_double(*repos: str, **state: Unpack[_RepoState]) -> GitHubDouble:
    return forge_holding(*(repos or ("acme/widget",)), branch="feat/thing", commit=_COMMIT, **state)


def _writable_movement(hub: HubHarness) -> IWriteChunkMovementRepository:
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
    """Mint the packaged adv-dwf graph, ingest a chunk (which pins to the hub's own
    packaged **default** graph — ingest names no graph), then repin it onto adv-dwf via
    ``PATCH /chunks/{id}`` (legal while the chunk is still ``not_ready``) before claiming
    a route, so every node id resolved off the mint response is the one the claimed
    chunk's pin actually recognizes."""
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
    make_ready(hub, chunk_id)
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def seed_at_deliver_with_an_unlanded_commit(
    hub: HubHarness, chunk_id: str, nodes: dict[str, str], *, repos: tuple[str, ...] = ("acme/widget",)
) -> None:
    """Place the chunk's current node directly at ``deliver`` (standing in for the six
    node-steps a real chunk would take to arrive here), carrying
    one ``git_commit`` artifact for a repo with no ``merged/<repo>`` marker, so this
    reads as a genuine, unlanded delivery attempt."""
    commit_artifacts = [
        StoredArtifact(
            kind=ArtifactKind.GIT_COMMIT,
            name=repo.split("/")[1],
            data=f"feat/thing:{_COMMIT}",
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
        close_decision=decide_close_enqueue([WorkRef("default", "1")], lambda _: True),
        admission=EpochAdmission.AT_OR_ABOVE,
    )
