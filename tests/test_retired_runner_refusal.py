"""A retired runner is refused inside each guarded domain operation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.registry import IWriteRunnerRegistry, RunnerRegistration, RunnerRetired
from tests.support import HubHarness, build_hub

pytestmark = pytest.mark.component

_RUNNER = "runner-a"


def _retired_hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    hub.services.fleet.register(_RUNNER, "ws-a")
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle(_RUNNER, retired=True, at=hub.clock.now(), by="op")
    return hub


def _registration(hub: HubHarness) -> RunnerRegistration:
    registration = hub.services.registry.get_runner(_RUNNER)
    assert registration is not None
    return registration


def test_register_refuses_a_retired_runner_before_any_write(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    before = _registration(hub)

    with pytest.raises(RunnerRetired):
        hub.services.fleet.register(_RUNNER, "ws-b", env_capacity=9)

    after = _registration(hub)
    assert after.workspace_id == before.workspace_id
    assert after.env_capacity == before.env_capacity


def test_heartbeat_refuses_a_retired_runner_without_refreshing_liveness(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    before = _registration(hub).last_seen_at
    hub.clock.advance(timedelta(seconds=60))

    with pytest.raises(RunnerRetired):
        hub.services.fleet.heartbeat(_RUNNER)

    assert _registration(hub).last_seen_at == before


def test_heartbeat_on_an_unregistered_runner_is_false_not_a_refusal(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.services.fleet.heartbeat("ghost") is False


def test_enroll_refuses_a_retired_runner_without_setting_a_token(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)

    with pytest.raises(RunnerRetired, match="reinstate"):
        hub.services.enrollment.enroll(_registration(hub))

    assert _registration(hub).token_hash is None


def test_claim_refuses_a_retired_runner_and_leaves_the_chunk_unrouted(tmp_path: Path) -> None:
    hub = _retired_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None

    with pytest.raises(RunnerRetired):
        hub.services.claim.claim(chunk, graph, runner_id=_RUNNER, workspace_id="ws-a", environment_ids=["e1"])

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"] is None
