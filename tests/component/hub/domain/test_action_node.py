"""A hub node naming an action: its mint rules, its due-ness, and the flow driver re-driving it."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import sqlalchemy as sa

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.domain.chunk.model import decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.store import schema as s
from tests.support import build_hub, default_tenant, pointer_token, report_lease

# Component — hub-advance re-drives a chunk waiting at a node naming an action


_ACTION_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build the change.
    judgement:
      prompt: Assess the build.
      choices:
        pass: {description: Green., to: deliver}
  deliver:
    executor: hub
    action: land
    poll_interval: 30
    poll_timeout: 10800
    judgement:
      choices:
        landed: {description: Landed., to: done}
        conflict: {description: Conflicted., to: build}
        failure: {description: Failed., to: build}
        inherited-failure: {description: Base is red., to: build}
"""


def test_the_flow_driver_re_drives_a_chunk_waiting_at_an_action_node_once_its_hint_passes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _ACTION_GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    deliver_view = next(n for n in minted.json()["nodes"] if n["name"] == "deliver")
    assert (deliver_view["action"], deliver_view["action_args"]) == ("land", {})
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "7"})]}
    ).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert deliver.action == "land"
    # Stand at `deliver`, waiting, as a pass that answered `pending` with a hint leaves it.
    cast(IWriteChunkMovementRepository, hub.services.chunks.movement).record_transition(
        transition_id="tr_test_deliver",
        close_decision=decide_close_enqueue(chunk.work_refs, lambda _: True),
        chunk_id=chunk_id,
        from_node_id=build_node_id,
        to_node_id=deliver.node_id,
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=hub.clock.now(),
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    hint = hub.clock.now() + timedelta(minutes=5)
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(s.hub_node_poll).values(
                chunk_id=chunk_id,
                node_id=deliver.node_id,
                epoch=1,
                polled_at=hub.clock.now(),
                next_poll_at=hint,
                tenant_id=default_tenant(hub.engine),
            )
        )

    # Short of its hint, a tick takes no turn for it: it waits at its next poll.
    hub.clock.advance(timedelta(seconds=31))
    assert hub.turn_outcome(chunk_id) is None
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["pending"]["next_poll_at"] == iso_utc(hint)

    hub.clock.advance(timedelta(minutes=5))
    assert hub.turn_outcome(chunk_id) is not None


_GARDEN_GRAPH_YAML = """
name: garden-delivery
entry: deliver
nodes:
  deliver:
    executor: hub
    action: garden-deliver
    with: {delta: delta, proposals: [docket, more]}
    judgement:
      choices:
        recorded: {description: Recorded., to: done}
        invalid: {description: Invalid., to: done}
        invalid-proposals: {description: Invalid docket., to: done}
        failure: {description: Failed., to: done}
"""


def test_a_nodes_action_and_its_arguments_round_trip_through_the_store_and_the_graph_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    minted = hub.client.post("/api/graphs", json={"definition_yaml": _GARDEN_GRAPH_YAML})
    assert minted.status_code == 201, minted.text
    graph = hub.services.graphs.get(minted.json()["graph_id"])
    assert graph is not None
    deliver = graph.node_by_name("deliver")
    assert deliver is not None
    assert (deliver.action, dict(deliver.action_args)) == (
        "garden-deliver",
        {"delta": ("delta",), "proposals": ("docket", "more")},
    )
    view = hub.client.get(f"/api/graphs/{graph.graph_id}").json()
    assert view["nodes"][0]["action_args"] == {"delta": ["delta"], "proposals": ["docket", "more"]}
