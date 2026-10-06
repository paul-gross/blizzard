"""Component checks for the owning lease on mint and subsequent writes.

A different lease is displaced; a lease-less write falls back to runner identity
(``bzh:epoch-fencing``). Warn-mode route tokens isolate the lease check."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import func, select

from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.store import schema as s
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.chunk_status_cache import ReadThroughChunkViews
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.leases.worker_stdout import WorkerStdoutFiles
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.lifecycle.env_release import EnvironmentRelease
from blizzard.runner.lifecycle.judgement.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.judgement.judgement import Judgement
from blizzard.runner.lifecycle.session import HarnessSelector
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Fill
from blizzard.runner.process.worker_scratch import WorkerScratchDirs
from tests.runner_fakes import (
    FakeHarness,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    make_session_resolver,
    make_store,
    make_stores,
    make_usage_recorder,
    registered_identity,
)
from tests.support import HubHarness, RunnerFleetClient, build_hub, ingest

pytestmark = pytest.mark.component

_HANDLE = WorkerHandle(session_id="sess-1", pid=200, process_start_time="start-200", pgid=200)

_YAML = """
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
          to: review
  review:
    executor: runner
    prompt: |
      Review the build.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Reviewed.
          to: done
"""


class _RecordingClient:
    """The hub's test client, keeping every body the runner posts through it."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self.posts: list[tuple[str, Any]] = []
        self.outcomes: list[Any] = []

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        self.posts.append((path, kwargs.get("json")))
        resp = self._client.post(path, **kwargs)
        if path.endswith("/completions"):
            self.outcomes.append(resp.json())
        return resp

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def bodies(self, suffix: str) -> list[Any]:
        return [body for path, body in self.posts if path.endswith(suffix)]


def _hub_with_chunk(tmp_path: Path) -> tuple[HubHarness, str, dict[str, str]]:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_WARN)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
    assert graph.status_code == 201, graph.text
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}
    return hub, ingest(hub, [{"source": "default", "ref": "1"}]), nodes


class _RealRunner:
    """A real runner — its own store, a real FILL tick and a real drain — over the real hub."""

    def __init__(self, tmp_path: Path, hub: HubHarness, runner_id: str) -> None:
        self.store = make_store(f"sqlite:///{tmp_path / f'{runner_id}.db'}")
        assert hub.app is not None
        # The hub resolves this runner from its own token, so it presents ``runner_id``'s.
        self.recording = _RecordingClient(
            RunnerFleetClient(hub.app, services=hub.services, default_runner_id=runner_id)
        )
        hub_client = HttpHubClient(cast(httpx.Client, self.recording))
        provider = FakeProvider({"e1": "/ws/e1"})
        harness = FakeHarness(handle=_HANDLE, verdict=None)
        harnesses = HarnessRegistry(
            {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
        )
        self.ctx = LoopContext(
            stores=make_stores(self.store),
            clock=hub.clock,
            hub=hub_client,
            chunk_views=ReadThroughChunkViews(hub_client),
            provider=provider,
            process=FakeProbe(alive={(200, "start-200")}),
            worktree_git=FakeWorktreeGit(),
            config=LoopConfig(runner_name=runner_id, workspace_id="w1", max_agents=1),
            identity=registered_identity(runner_id, runner_id),
            worker_files=WorkerStdoutFiles("", self.store),
            elicitation_files=ElicitationFiles(str(tmp_path / f"{runner_id}-elicit")),
            worker_scratch=WorkerScratchDirs(""),
            usage=make_usage_recorder(self.store, hub.clock),
            sessions=make_session_resolver(self.store),
            harness_selector=HarnessSelector(harnesses=harnesses),
            env_release=EnvironmentRelease(environments=self.store, clock=hub.clock, provider=provider),
            harnesses=harnesses,
        )

    def claim_and_mint(self, chunk_id: str) -> tuple[str, int]:
        """A real FILL tick: claim, mint, buffer the ``lease.minted`` — nothing drained yet."""
        Fill(self.ctx).run()
        lease = self.store.active_lease_for_chunk(chunk_id)
        assert lease is not None, "the FILL tick minted no lease"
        return lease.lease_id, lease.epoch

    def buffer_completion(self, chunk_id: str) -> None:
        """Buffer the lease's completion the way its judgement does."""
        lease = self.store.active_lease_for_chunk(chunk_id)
        assert lease is not None
        envelope = self.ctx.hub.get_envelope(chunk_id)
        Judgement(self.ctx, lease, envelope, self.store.bindings_for_chunk(chunk_id))._buffer_completion("pass", [], [])

    def drain(self) -> list[int]:
        """Drain once; the seqs that were buffered going in."""
        buffered = [fact.seq for fact in self.store.pending_outbound()]
        OutboundDrain(self.ctx).run()
        return buffered

    def still_buffered(self, seqs: list[int]) -> list[int]:
        return [fact.seq for fact in self.store.pending_outbound() if fact.seq in seqs]


def _lease_rows(hub: HubHarness, chunk_id: str) -> list[tuple[str, int, str | None]]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.lease_facts.c.runner_id, s.lease_facts.c.epoch, s.lease_facts.c.lease_id).where(
                s.lease_facts.c.chunk_id == chunk_id
            )
        ).all()
    return [(r.runner_id, r.epoch, r.lease_id) for r in rows]


def _assert_refused_and_drained(
    runner: _RealRunner, hub: HubHarness, chunk_id: str, lease_id: str, *, buffered: list[int]
) -> None:
    """The buffered mint and completion each carried the lease id, each was refused, and the
    drain acked both rather than wedging on them."""
    events = runner.recording.bodies("/events")
    mints = [f for body in events for f in body["facts"] if f["kind"] == "lease.minted"]
    assert [f["payload"]["lease_id"] for f in mints] == [lease_id]
    completions = runner.recording.bodies("/completions")
    assert [c["lease_id"] for c in completions] == [lease_id]
    assert [c["outcome"] for c in runner.recording.outcomes] == ["failure"]

    assert lease_id not in [row[2] for row in _lease_rows(hub, chunk_id)]
    assert len(buffered) == 2
    assert runner.still_buffered(buffered) == []


# --- P2-a: a real runner's buffered mint across a restart or a reclaim ------------------


def test_a_buffered_mint_across_a_restart_is_refused_with_its_completion(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _hub_with_chunk(tmp_path)
    a = _RealRunner(tmp_path, hub, "rA")
    lease_id, epoch = a.claim_and_mint(chunk_id)
    assert epoch == 1  # the claim's own reservation
    a.buffer_completion(chunk_id)

    restart = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={"by": "operator"})
    assert restart.status_code == 202, restart.text
    buffered = a.drain()

    _assert_refused_and_drained(a, hub, chunk_id, lease_id, buffered=buffered)
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_id"] == nodes["build"]
    with hub.engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(s.transitions).where(s.transitions.c.chunk_id == chunk_id)
            ).scalar_one()
            == 0
        )


def test_a_buffered_mint_across_a_reclaim_is_refused_with_its_completion(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _hub_with_chunk(tmp_path)
    a = _RealRunner(tmp_path, hub, "rA")
    lease_id, _ = a.claim_and_mint(chunk_id)
    a.buffer_completion(chunk_id)

    assert hub.client.post(f"/api/chunks/{chunk_id}/detach").status_code == 202
    b = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "rB", "workspace_id": "w2", "environment_ids": []},
    )
    assert b.status_code == 201, b.text
    buffered = a.drain()

    _assert_refused_and_drained(a, hub, chunk_id, lease_id, buffered=buffered)
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["current_node_id"] == nodes["build"]
    assert detail["route"]["runner_id"] == "rB"


def test_a_live_runners_mint_records_its_lease_and_its_completion_advances(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _hub_with_chunk(tmp_path)
    a = _RealRunner(tmp_path, hub, "rA")
    lease_id, epoch = a.claim_and_mint(chunk_id)
    a.buffer_completion(chunk_id)
    buffered = a.drain()

    assert _lease_rows(hub, chunk_id) == [("rA", epoch, lease_id)]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == nodes["review"]
    assert a.still_buffered(buffered) == []


# --- P2-b: the owning lease is matched when both sides name one --------------------------


def _minted(tmp_path: Path, *, lease_id: str) -> tuple[HubHarness, str, dict[str, str]]:
    """rA claims and reports its mint at epoch 1 naming ``lease_id`` — the epoch's owning lease."""
    hub, chunk_id, nodes = _hub_with_chunk(tmp_path)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "rA", "workspace_id": "w", "environment_ids": []},
    )
    assert claim.status_code == 201, claim.text
    ack = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "rA",
            "facts": [
                {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1, "lease_id": lease_id}}
            ],
        },
    ).json()
    assert ack["applied"] == [1], ack
    return hub, chunk_id, nodes


def _complete(hub: HubHarness, chunk_id: str, node_id: str, *, lease_id: str | None) -> dict:
    body: dict[str, object] = {"choice": "pass", "epoch": 1, "runner_id": "rA", "from_node_id": node_id}
    if lease_id is not None:
        body["lease_id"] = lease_id
    resp = hub.client.post(f"/api/fleet/chunks/{chunk_id}/completions", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_completion_naming_another_lease_at_a_bound_epoch_is_refused(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _minted(tmp_path, lease_id="lease_owner")

    result = _complete(hub, chunk_id, nodes["build"], lease_id="lease_other")

    assert result["outcome"] == "failure", result
    assert "epoch 1" in result["detail"]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == nodes["build"]


def test_a_decision_and_a_question_naming_another_lease_are_refused(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _minted(tmp_path, lease_id="lease_owner")

    decision = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/decisions",
        json={"from_node_id": nodes["build"], "epoch": 1, "runner_id": "rA", "lease_id": "lease_other"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["outcome"] == "failure", decision.json()
    question = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_other",
            "chunk_id": chunk_id,
            "runner_id": "rA",
            "epoch": 1,
            "lease_id": "lease_other",
            "question": "Which API?",
            "options": [],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert question.status_code == 409, question.text


def test_a_second_mint_naming_another_lease_at_a_bound_epoch_is_refused(tmp_path: Path) -> None:
    hub, chunk_id, _ = _minted(tmp_path, lease_id="lease_owner")

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "rA",
            "facts": [
                {
                    "seq": 99,
                    "kind": "lease.minted",
                    "payload": {"chunk_id": chunk_id, "epoch": 1, "lease_id": "lease_other"},
                }
            ],
        },
    )

    assert resp.json()["rejected"] == [99], resp.text
    assert [r[2] for r in _lease_rows(hub, chunk_id)] == ["lease_owner"]


def test_the_owning_lease_and_a_lease_less_write_from_its_runner_are_admitted(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _minted(tmp_path, lease_id="lease_owner")

    assert _complete(hub, chunk_id, nodes["build"], lease_id=None)["outcome"] == "next"


def test_a_completion_naming_the_owning_lease_is_admitted(tmp_path: Path) -> None:
    hub, chunk_id, nodes = _minted(tmp_path, lease_id="lease_owner")

    assert _complete(hub, chunk_id, nodes["build"], lease_id="lease_owner")["outcome"] == "next"
