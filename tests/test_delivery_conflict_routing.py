"""#241 AC2/AC3: the SHIPPED ``advanced-development-workflow`` graph's ``deliver`` node
actually routes a printed ``conflict`` outcome — component tier.

Mints the real, packaged graph, seeding the chunk directly at ``deliver``'s minted node
id via a direct transition-fact insert.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.ids import ARTIFACT_PREFIX, mint
from blizzard.hub.delivery.command_runner import CommandResult
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import DEFAULT_BOUNCE_CAP
from blizzard.hub.domain.work import IWriteChunkRepository
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.graphs.scripts import land_pr_ci
from tests.support import FakeHubCommandRunner, FakeHubWorkdir, HubHarness, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_LAND_COMMAND = "python3 -m blizzard.hub.graphs.scripts.land_pr_ci"


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    """A test-only cast: ``HubHarness.services.chunks`` is read-typed
    (``bzh:controller-read-only``), but the live object is always the write-capable
    ``ChunkStore`` — mirrors ``tests/test_hub_command_node.py``'s own helper."""
    return cast(IWriteChunkRepository, hub.services.chunks)


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
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    return chunk_id, nodes


def _seed_at_deliver_with_an_unlanded_commit(hub: HubHarness, chunk_id: str, nodes: dict[str, str]) -> None:
    """Place the chunk's current node directly at ``deliver`` (standing in for the six
    node-steps a real chunk would take to arrive here — same technique
    ``tests/test_delivery_incomplete_routing.py`` uses for ``retrospective``), carrying
    one ``git_commit`` artifact for a repo with no ``merged/<repo>`` marker, so this
    reads as a genuine, unlanded delivery attempt."""
    commit_artifact = ArtifactRow(
        kind=ArtifactKind.GIT_COMMIT,
        name="w",
        data=f"feat/thing:{'c' * 40}",
        repo="acme/widget",
        forge=None,
        artifact_id=mint(ARTIFACT_PREFIX, hub.clock),
        chunk_id=chunk_id,
        node_id=nodes["build"],
        node_name="build",
        epoch=1,
    )
    _writable(hub).record_transition(
        transition_id="tr_seed_deliver",
        chunk_id=chunk_id,
        from_node_id=None,
        to_node_id=nodes["deliver"],
        choice_name=None,
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=[commit_artifact],
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
        e for e in hub.services.chunks.list_events(chunk_id=chunk_id) if e.kind == "hub-node-unroutable-outcome"
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
        _writable(hub).record_bounce(chunk_id, epoch=epoch, cause="conflict", envelope="{}", at=hub.clock.now())
    report_lease(hub, chunk_id, epoch=DEFAULT_BOUNCE_CAP + 1, seq=1)

    advance = hub.client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    body = advance.json()
    assert body["ran"] is True

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "needs_human"
    assert len(detail["bounces"]) == DEFAULT_BOUNCE_CAP + 1
