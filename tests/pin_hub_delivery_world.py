"""Helpers for the pin hub delivery tests, shared with other test modules."""

from __future__ import annotations

from typing import cast

from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.graphs import PACKAGED
from tests.support import HubHarness, pointer_token


def writable(hub: HubHarness) -> IWriteChunkMovementRepository:
    return cast(IWriteChunkMovementRepository, hub.services.chunks.movement)


def mint_and_claim(hub: HubHarness) -> tuple[str, dict[str, str]]:
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
