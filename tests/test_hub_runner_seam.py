"""The hub<->runner seam, end to end at the component tier (issue #38, slice 6).

Slice 2 (the hub endpoint) and slice 4 (the runner PULL step) each mock the other
side — the hub's tests never call a runner, and the runner's tests drive a
``FakeHub``. Nothing yet proves the two halves agree on the wire. This test drives
the **real** hub app and the **real** ``pull`` step in one process: ``POST
/chunks/{id}/detach`` against the real FastAPI app, then a real PULL tick reads it
back through ``HttpHubClient`` — the very same production adapter the daemon runs —
wrapping the hub's own ``TestClient`` (itself an ``httpx.Client``, so no new
production code or wiring was needed to point the runner's outbound-only hub client
at the in-process hub).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.internal.http_hub import HttpHubClient
from blizzard.runner.loop.steps import pull
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import FakeHarness, FakeProbe, FakeProvider, FakeWorktreeGit, make_store
from tests.support import build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "38"}
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")

# A gateless build -> deliver graph, minimal enough to claim and lease against.
_PLAIN_YAML = """
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
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def test_detach_at_the_real_hub_is_learned_by_a_real_pull_tick(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _PLAIN_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "ws1", "environment_ids": ["e1"]},
    )
    assert claim.status_code == 201, claim.text
    envelope = claim.json()["envelope"]
    node = envelope["node"]
    report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="r1")
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"

    # Seed the runner-side store with the in-flight lease + binding a real runner
    # would already hold from claim/spawn time. Stamped off the same clock the hub
    # uses, so the store's own held-binding predicate (timestamp-ordered releases,
    # `bzh:facts-not-status`) sees a coherent history once the clock advances below.
    seed_time = hub.clock.now()
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id=chunk_id,
            graph_id=envelope["graph_id"],
            node_id=node["node_id"],
            node_name=node["node_name"],
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=seed_time,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=seed_time)
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=seed_time)

    # The operator detaches at the REAL hub endpoint.
    hub.clock.advance(timedelta(seconds=1))
    detach = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert detach.status_code == 202, detach.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

    # The live runner learns of it on its own next PULL, driven against the REAL hub
    # app in-process: `HttpHubClient` (the production `IHubClient` adapter) wraps the
    # hub's own `TestClient`, which is itself an `httpx.Client` — no fake, no mock
    # transport, the real ASGI app end to end.
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = LoopContext(
        store=store,
        clock=hub.clock,
        hub=HttpHubClient(hub.client),
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        process=probe,
        worktree_git=FakeWorktreeGit(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
    )

    pull(ctx)

    assert probe.killed == [100]  # worker killed
    assert provider.released == ["e1"]  # environment released
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.live_tenure_chunk_ids() == []  # no lingering tenure for FILL to trip on

    # The seam holds both ways: the chunk still derives `ready` at the real hub.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
