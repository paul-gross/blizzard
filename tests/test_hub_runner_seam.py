"""The hub<->runner seam, end to end at the component tier (slice 6).

Slice 2 and slice 4 each mock the other side, so nothing yet proves the two halves
agree on the wire. Drives the real hub app and real ``pull`` step in one process:
``POST /chunks/{id}/detach``/``/stop``, then a PULL tick reads it back for real.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.tenancy.administration import DEFAULT_PROJECT_SLUG
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Fill, Pull
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs
from blizzard.runner.usage.recorder import UsageRecorder
from tests.runner_fakes import (
    FIXTURE_PROJECT_ID,
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    SqlAlchemyRunnerStore,
    fixed_hub_token,
    make_host_stores,
    make_store,
    make_stores,
    matched_verdict,
    registered_identity,
    serving,
    single_workspace,
)
from tests.support import HubHarness, build_hub, pointer_token, report_lease

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "38"}
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)

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


def _claude_code_registry() -> HarnessRegistry:
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    return HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    )


def _ctx(
    hub: HubHarness,
    store: SqlAlchemyRunnerStore,
    provider: FakeProvider,
    probe: FakeProbe,
    tmp_path: Path,
    *,
    identity: RunnerIdentityHolder,
    harnesses: HarnessRegistry | None = None,
    project: str = FIXTURE_PROJECT_ID,
) -> LoopContext:
    """A loop context over the in-process hub; every other seam a double."""
    registry = harnesses if harnesses is not None else _claude_code_registry()
    hub_client = HttpHubClient(hub.client)
    return LoopContext(
        stores=make_stores(store),
        host_stores=make_host_stores(store),
        clock=hub.clock,
        hub=hub_client,
        token_identity=hub_client,
        identity_verdict=matched_verdict(),
        hub_token=fixed_hub_token(),
        chunk_views=ReadThroughChunkViews(hub_client),
        workspaces=single_workspace(provider),
        process=probe,
        worktree_git=FakeWorktreeGit(),
        config=LoopConfig(
            runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(1, project=project), agents=1
        ),
        identity=identity,
        worker_files=WorkerStdoutFiles("", store),
        elicitation_files=ElicitationFiles(str(tmp_path / "elicit")),
        worker_scratch=WorkerScratchDirs(""),
        usage=UsageRecorder(
            leases=store,
            usage=store,
            clock=hub.clock,
            worker_files=WorkerStdoutFiles("", store),
            workspaces=single_workspace(provider),
            harnesses=registry,
            invocation_boundaries=store,
        ),
        sessions=SessionResolver(leases=store, harnesses=registry),
        harness_selector=HarnessSelector(harnesses=registry),
        env_release=EnvironmentRelease(environments=store, clock=hub.clock, workspaces=single_workspace(provider)),
        harnesses=registry,
    )


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
    # would hold, stamped off the same clock the hub uses (`bzh:facts-not-status`).
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
            retries_max=2,
            created_at=seed_time,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=seed_time,
    )
    store.record_binding(
        chunk_id=chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=seed_time, workspace_id="ws1"
    )

    # The operator detaches at the REAL hub endpoint.
    hub.clock.advance(timedelta(seconds=1))
    detach = hub.client.post(f"/api/chunks/{chunk_id}/detach")
    assert detach.status_code == 202, detach.text
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

    # The live runner learns of it on its own next PULL, against the REAL hub app
    # in-process, end to end.
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(hub, store, provider, probe, tmp_path, identity=registered_identity("r1", "r1"))

    Pull(ctx).run()

    assert probe.killed == [100]  # worker killed
    assert provider.released == ["e1"]  # environment released
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.live_tenure_chunk_ids() == []  # no lingering tenure for FILL to trip on

    # The seam holds both ways: the chunk still derives `ready` at the real hub.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"


def test_stop_at_the_real_hub_is_learned_by_a_real_pull_tick(tmp_path: Path) -> None:
    """The consider-7 gap from the #118 pre-push review: drives the actual tick that
    frees the environments, so a regression in either the hub's route release or the
    runner's must-fix-1 status branch fails here."""
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
            retries_max=2,
            created_at=seed_time,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=seed_time,
    )
    store.record_binding(
        chunk_id=chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=seed_time, workspace_id="ws1"
    )

    # The operator stops at the REAL hub endpoint.
    hub.clock.advance(timedelta(seconds=1))
    stop = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"})
    assert stop.status_code == 202, stop.text
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert detail["route"] is None  # released in the same atomic write as the stop fact

    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(hub, store, provider, probe, tmp_path, identity=registered_identity("r1", "r1"))

    Pull(ctx).run()

    assert probe.killed == [100]  # worker killed
    assert provider.released == ["e1"]  # environment released
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.live_tenure_chunk_ids() == []  # no lingering tenure for FILL to trip on

    # Unlike detach, the chunk stays terminal — it never re-derives `ready`.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "stopped"


def test_claim_revalidates_against_a_regressed_registration_through_the_real_runner_loop(tmp_path: Path) -> None:
    """A registration regressed between ``Pull`` and ``Fill`` is denied by the hub; the next ``Pull`` recovers."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _PLAIN_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_harnesses": ["opencode"]}).status_code == 202
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    opencode = FakeHarness(handle=_HANDLE, verdict=None)
    claude = FakeHarness(handle=_HANDLE, verdict=None)
    harnesses = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude, transcript_source=claude.transcript_source()),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode, transcript_source=opencode.transcript_source()),
        }
    )
    ctx = _ctx(
        hub,
        store,
        FakeProvider({"e1": "/ws/e1"}),
        FakeProbe(alive=set()),
        tmp_path,
        identity=RunnerIdentityHolder(),
        harnesses=harnesses,
        project=DEFAULT_PROJECT_SLUG,
    )

    Pull(ctx).run()
    regressed = hub.client.post(
        "/api/fleet/runners",
        json={
            "name": "r1",
            "workspace_id": "ws1",
            "capabilities": [{"harness_id": CLAUDE_CODE_HARNESS_ID, "default": True}],
        },
    )
    assert regressed.status_code == 201, regressed.text
    assert regressed.json()["runner_id"] == "r1"

    Fill(ctx).run()

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"
    assert store.active_lease_for_chunk(chunk_id) is None

    Pull(ctx).run()
    Fill(ctx).run()

    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] != "ready"
    assert store.active_lease_for_chunk(chunk_id) is not None
