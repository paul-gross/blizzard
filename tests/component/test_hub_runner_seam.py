"""The hub<->runner seam, end to end at the component tier (slice 6).

Slice 2 and slice 4 each mock the other side, so nothing yet proves the two halves
agree on the wire. Drives the real hub app and real ``pull`` step in one process:
``POST /chunks/{id}/detach``/``/stop``, then a PULL tick reads it back for real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.tenancy.administration import DEFAULT_PROJECT_SLUG
from blizzard.hub.store import schema as s
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.opencode.section import OPENCODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.chunk_status_cache import IChunkViews, ReadThroughChunkViews
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.session import HarnessSelector, SessionResolver
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Advance, Fill, Pull
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs
from blizzard.runner.usage.recorder import UsageRecorder
from tests.support.runner_fakes import (
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
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import HubHarness, build_hub, chunk_rows, hub_scoped, pointer_token, report_lease

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
    gates: tuple[str, ...] = (),
    chunk_views: IChunkViews | None = None,
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
        chunk_views=chunk_views if chunk_views is not None else ReadThroughChunkViews(hub_client),
        workspaces=single_workspace(provider),
        process=probe,
        worktree_git=FakeWorktreeGit(),
        config=LoopConfig(
            runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(project=project), agents=1, gates=gates
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
    store.record_mint(
        new_mint(
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
    store.record_mint(
        new_mint(
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


# --- a fresh claim carries on its node visit's gate decision ---------------------------------------

_ARTIFACT = {
    "name": "acme/widget",
    "kind": "git_commit",
    "repo": "acme/widget",
    "branch_name": "b",
    "commit_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
}


@dataclass
class _GatedChunk:
    """A chunk at ``build`` that runner ``r2`` claims and gates by hand at epoch 1, then detaches from."""

    hub: HubHarness
    chunk_id: str
    build: str
    decision_id: str

    @classmethod
    def detached_at_gate(cls, hub: HubHarness, *, resolve: str | None) -> _GatedChunk:
        graph = hub.client.post("/api/graphs", json={"definition_yaml": _PLAIN_YAML})
        assert graph.status_code == 201, graph.text
        build = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
        chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
        assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "r2", "workspace_id": "w1", "environment_ids": ["e"]},
        )
        assert claim.status_code == 201, claim.text
        report_lease(hub, chunk_id, epoch=1, seq=1, runner_id="r2")
        gate = hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/decisions",
            json={"from_node_id": build, "epoch": 1, "runner_id": "r2", "artifacts": [_ARTIFACT]},
        )
        assert gate.json()["outcome"] == "parked_at_gate", gate.text
        decision_id = str(hub.client.get(f"/api/chunks/{chunk_id}").json()["decision"]["decision_id"])
        if resolve is not None:
            resolved = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": resolve})
            assert resolved.status_code == 200, resolved.text
        assert hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code == 202
        return cls(hub, chunk_id, build, decision_id)

    def status(self) -> str:
        return str(self.hub.client.get(f"/api/chunks/{self.chunk_id}").json()["status"])

    def decision_ids(self) -> list[str]:
        with self.hub.engine.begin() as conn:
            return list(
                conn.execute(
                    hub_scoped(sa.select(s.decisions.c.decision_id).where(s.decisions.c.chunk_id == self.chunk_id))
                ).scalars()
            )

    def transition_decisions(self) -> list[str | None]:
        with self.hub.engine.begin() as conn:
            return list(
                conn.execute(
                    hub_scoped(sa.select(s.transitions.c.decision_id).where(s.transitions.c.chunk_id == self.chunk_id))
                ).scalars()
            )


def _runner(
    hub: HubHarness, tmp_path: Path, *, gates: tuple[str, ...] = (), chunk_views: IChunkViews | None = None
) -> tuple[LoopContext, SqlAlchemyRunnerStore, FakeHarness]:
    """The stock runner ``r1`` over the in-process hub, registered by a first PULL."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    )
    ctx = _ctx(
        hub,
        store,
        FakeProvider({"e1": "/ws/e1"}),
        FakeProbe(alive=set()),
        tmp_path,
        identity=RunnerIdentityHolder(),
        harnesses=registry,
        project=DEFAULT_PROJECT_SLUG,
        gates=gates,
        chunk_views=chunk_views,
    )
    Pull(ctx).run()
    return ctx, store, harness


@pytest.mark.parametrize("gates", [("build",), ()], ids=["gated-locally", "ungated-locally"])
def test_a_fresh_claim_consumes_a_resolved_decision_without_rerunning_the_step(
    tmp_path: Path, gates: tuple[str, ...]
) -> None:
    hub = build_hub(tmp_path)
    chunk = _GatedChunk.detached_at_gate(hub, resolve="pass")
    assert chunk.status() == "ready"
    artifacts = chunk_rows(hub, s.artifacts, chunk.chunk_id)
    ctx, store, harness = _runner(hub, tmp_path, gates=gates)

    Fill(ctx).run()
    Advance(ctx).run()

    assert harness.spawns == []
    assert store.active_lease_for_chunk(chunk.chunk_id) is None
    assert chunk.transition_decisions() == [chunk.decision_id]
    assert chunk.decision_ids() == [chunk.decision_id]  # no second gate
    assert chunk_rows(hub, s.artifacts, chunk.chunk_id) == artifacts


def test_a_binding_held_over_a_pending_decision_is_held_without_a_spawn(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _GatedChunk.detached_at_gate(hub, resolve=None)
    assert chunk.status() == "waiting_on_human"
    ctx, store, harness = _runner(hub, tmp_path)
    store.record_binding(
        chunk_id=chunk.chunk_id, environment_id="e1", workdir="/ws/e1", bound_at=hub.clock.now(), workspace_id="ws1"
    )

    Fill(ctx).run()
    Advance(ctx).run()

    assert harness.spawns == []
    assert store.active_lease_for_chunk(chunk.chunk_id) is None
    assert chunk.status() == "waiting_on_human"
    assert chunk.decision_ids() == [chunk.decision_id]


@dataclass
class _UnreadableOnce:
    """The runner's chunk views, failing the first read of a chunk after its claim invalidates it."""

    inner: IChunkViews
    armed: bool = False

    def get(self, chunk_id: str) -> Any:
        if self.armed:
            self.armed = False
            raise HubClientError("chunk view unreadable")
        return self.inner.get(chunk_id)

    def prime(self, chunk_ids: Any) -> None:
        self.inner.prime(chunk_ids)

    def invalidate(self, chunk_id: str) -> None:
        self.armed = True
        self.inner.invalidate(chunk_id)


def test_an_unreadable_view_after_a_won_claim_spawns_nothing_and_the_next_reconcile_holds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = _GatedChunk.detached_at_gate(hub, resolve="pass")
    views = _UnreadableOnce(ReadThroughChunkViews(HttpHubClient(hub.client)))
    ctx, store, harness = _runner(hub, tmp_path, chunk_views=views)

    Fill(ctx).run()

    assert harness.spawns == []
    assert store.held_environment_ids() == ["e1"]
    assert store.route_token(chunk.chunk_id) is not None

    Fill(ctx).run()  # the interrupted-claim reconcile reads the decision and holds

    assert harness.spawns == []
    assert store.active_lease_for_chunk(chunk.chunk_id) is None
    assert chunk.decision_ids() == [chunk.decision_id]


def test_a_fresh_claim_on_an_ungated_node_spawns_as_ever(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _PLAIN_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    ctx, store, harness = _runner(hub, tmp_path, gates=("build",))

    Fill(ctx).run()

    assert len(harness.spawns) == 1
    assert store.active_lease_for_chunk(chunk_id) is not None
