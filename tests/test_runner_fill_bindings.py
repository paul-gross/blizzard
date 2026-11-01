"""FILL shares one agent count across the runner's bindings: each free agent sends one peek naming the projects whose
workspace has a free environment, and the chunk the hub returns is claimed into its own project's workspace.

A runner here binds ``pr_a`` to ``ws_a`` and ``pr_b`` to ``ws_b``. The hub's ready-queue order alone picks the project;
a workspace with no free environment drops out of the peek; one found out of environments at acquisition drops out for
the rest of the tick; against a hub too old to narrow the peek, an entry outside the named projects ends the fill; and
two bindings resolving to one project stop every claim, named in the runner's status. Driven against a real (tmp
sqlite) runner store with fakes at the seams."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.provider import AcquiredEnvironment, WorkspaceAcquisitionError
from blizzard.runner.environments.workspaces import IWorkspaces, WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.client import BindingDeclaration, ClaimRequest, QueueEntry, RouteClaimOutcome
from blizzard.runner.hub.served_projects import ServedProject
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.routing import RoutingConflict
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Fill
from blizzard.runner.status.view import RunnerStatusService
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    registered_identity,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


class _Hub(FakeHub):
    """A fake hub that wins every claim, answering each chunk's own envelope."""

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        self.claim_outcome = claimed_outcome(
            claim.chunk_id, make_envelope(claim.chunk_id, "build", node_id="nd_build", choices=_CHOICES)
        )
        return super().claim_route(claim)


def _workspaces() -> WorkspaceRegistry:
    return WorkspaceRegistry(
        [
            WorkspaceEntry("ws_a", FakeProvider({"a1": "/root-a/a1", "a2": "/root-a/a2"}), spawn_root="/root-a"),
            WorkspaceEntry("ws_b", FakeProvider({"b1": "/root-b/b1", "b2": "/root-b/b2"}), spawn_root="/root-b"),
        ]
    )


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _bindings() -> tuple[BindingDeclaration, ...]:
    # Each binding's own `agents` is the runner's count here; the fill reads the runner's one count, never these.
    return (BindingDeclaration("pr_a", "ws_a", 2), BindingDeclaration("pr_b", "ws_b", 2))


def _ctx(
    store: SqlAlchemyRunnerStore,
    hub: FakeHub,
    bindings: tuple[BindingDeclaration, ...],
    *,
    agents: int = 2,
    workspaces: IWorkspaces | None = None,
) -> LoopContext:
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=bindings, agents=agents),
        workspaces=workspaces or _workspaces(),
    )


def _entry(chunk_id: str, project_id: str, position: int = 0) -> QueueEntry:
    return QueueEntry(chunk_id=chunk_id, graph_id="gr_1", position=position, project_id=project_id)


def _hold_open_lease(
    store: SqlAlchemyRunnerStore,
    chunk_id: str,
    *,
    workspace_id: str | None,
    project_id: str | None,
    environment_id: str = "a1",
    workdir: str = "/root-a/a1",
) -> None:
    store.record_lease(
        NewLease(
            lease_id=f"lease_{chunk_id}",
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_binding(
        chunk_id=chunk_id,
        environment_id=environment_id,
        workdir=workdir,
        bound_at=_NOW,
        workspace_id=workspace_id,
        project_id=project_id,
    )


def _peeked_projects(hub: FakeHub) -> list[list[str] | None]:
    return [call.projects for call in hub.peek_queue_requests]


class _StaleHintProvider(FakeProvider):
    """Lists a pool the pool-held read shows free, yet refuses every acquisition — the hint run ahead of the truth."""

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        raise WorkspaceAcquisitionError("no environment free")


def test_the_hubs_queue_order_alone_picks_the_project_each_free_agent_works(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = _Hub()
    hub.queue_responses = [[_entry("ch_w1", "pr_b")], [_entry("ch_d1", "pr_a")], [_entry("ch_d2", "pr_a")]]

    Fill(_ctx(store, hub, _bindings(), agents=2)).run()

    assert _peeked_projects(hub) == [["pr_a", "pr_b"], ["pr_a", "pr_b"]]
    assert [(c.chunk_id, c.workspace_id) for c in hub.claims] == [("ch_w1", "ws_b"), ("ch_d1", "ws_a")]
    held = {b.chunk_id: (b.workspace_id, b.project_id, b.workdir) for b in store.held_bindings()}
    assert held == {"ch_w1": ("ws_b", "pr_b", "/root-b/b1"), "ch_d1": ("ws_a", "pr_a", "/root-a/a1")}


def test_open_leases_are_counted_against_the_one_runner_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _hold_open_lease(store, "ch_running", workspace_id="ws_a", project_id="pr_a")
    hub = _Hub()
    hub.queue = [_entry("ch_b", "pr_b")]

    Fill(_ctx(store, hub, _bindings(), agents=1)).run()
    assert hub.peek_queue_requests == [] and hub.claims == []

    Fill(_ctx(store, hub, _bindings(), agents=2)).run()
    assert _peeked_projects(hub) == [["pr_a", "pr_b"]]
    assert [c.chunk_id for c in hub.claims] == ["ch_b"]


def test_a_held_row_of_unknown_workspace_counts_against_the_runner_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _hold_open_lease(store, "ch_running", workspace_id=None, project_id=None)
    hub = _Hub()
    hub.queue = [_entry("ch_a2", "pr_a", 0), _entry("ch_b", "pr_b", 1)]

    Fill(_ctx(store, hub, _bindings(), agents=1)).run()

    assert hub.peek_queue_requests == [] and hub.claims == []


def test_a_workspace_with_no_free_environment_is_left_out_of_the_peek(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _hold_open_lease(store, "ch_b1", workspace_id="ws_b", project_id="pr_b", environment_id="b1", workdir="/root-b/b1")
    _hold_open_lease(store, "ch_b2", workspace_id="ws_b", project_id="pr_b", environment_id="b2", workdir="/root-b/b2")
    hub = _Hub()
    hub.queue = [_entry("ch_a", "pr_a")]

    Fill(_ctx(store, hub, _bindings(), agents=3)).run()

    assert _peeked_projects(hub) == [["pr_a"]]
    assert [(c.chunk_id, c.workspace_id) for c in hub.claims] == [("ch_a", "ws_a")]


def test_no_workspace_with_a_free_environment_sends_no_peek(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for env in ("a1", "a2"):
        _hold_open_lease(
            store, f"ch_{env}", workspace_id="ws_a", project_id="pr_a", environment_id=env, workdir=f"/root-a/{env}"
        )
    for env in ("b1", "b2"):
        _hold_open_lease(
            store, f"ch_{env}", workspace_id="ws_b", project_id="pr_b", environment_id=env, workdir=f"/root-b/{env}"
        )
    hub = _Hub()
    hub.queue = [_entry("ch_a", "pr_a")]

    Fill(_ctx(store, hub, _bindings(), agents=9)).run()

    assert hub.peek_queue_requests == [] and hub.claims == []


def test_a_workspace_found_out_of_environments_at_acquisition_is_dropped_for_the_rest_of_the_tick(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    workspaces = WorkspaceRegistry(
        [
            WorkspaceEntry("ws_a", FakeProvider({"a1": "/root-a/a1"}), spawn_root="/root-a"),
            WorkspaceEntry("ws_b", _StaleHintProvider({"b1": "/root-b/b1"}), spawn_root="/root-b"),
        ]
    )
    hub = _Hub()
    hub.queue_responses = [[_entry("ch_w1", "pr_b")], [_entry("ch_d1", "pr_a")]]

    Fill(_ctx(store, hub, _bindings(), agents=2, workspaces=workspaces)).run()

    assert _peeked_projects(hub) == [["pr_a", "pr_b"], ["pr_a"]]
    assert [(c.chunk_id, c.workspace_id) for c in hub.claims] == [("ch_d1", "ws_a")]


def test_an_entry_outside_the_named_projects_from_an_unfiltered_hub_ends_the_fill(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = _Hub()
    hub.narrows_peek = False  # a hub too old to honor `projects` answers any served project's head
    hub.queue = [_entry("ch_x", "pr_other")]

    Fill(_ctx(store, hub, _bindings(), agents=2)).run()

    assert _peeked_projects(hub) == [["pr_a", "pr_b"]]
    assert hub.claims == []


def test_two_bindings_resolving_to_one_project_claim_nothing_and_status_names_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bindings = (BindingDeclaration("pr_a", "ws_a", 2), BindingDeclaration("alpha", "ws_b", 2))
    hub = _Hub()
    hub.queue = [_entry("ch_a", "pr_a")]

    ctx = _ctx(store, hub, bindings)
    store.record_served_projects(
        [
            ServedProject("pr_a", project_id="pr_a", slug="alpha"),
            ServedProject("alpha", project_id="pr_a", slug="alpha"),
        ],
        at=_NOW,
    )

    Fill(ctx).run()

    assert hub.peek_queue_requests == [] and hub.claims == []
    status = RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity(),
        served_projects=store,
        runner_name="r1",
        workspace_id="ws_a",
        bindings=bindings,
        hub_url="http://hub",
        harnesses=HarnessRegistry(
            {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=FakeHarness(handle=_HANDLE, verdict=None))}
        ),
        workspaces=_workspaces(),
    )
    summary = status.summary()
    assert summary.routing_conflicts == (RoutingConflict(project_id="pr_a", entries=("pr_a", "alpha")),)
    assert [(b.project, b.project_id, b.workspace_id) for b in summary.capacities.bindings] == [
        ("pr_a", "pr_a", "ws_a"),
        ("alpha", "pr_a", "ws_b"),
    ]


def test_status_counts_capacity_across_every_bound_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = _Hub()
    hub.queue_responses = [[_entry("ch_a", "pr_a")], [_entry("ch_b", "pr_b")]]
    Fill(_ctx(store, hub, _bindings(), agents=2)).run()
    status = RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity(),
        served_projects=store,
        runner_name="r1",
        workspace_id="ws_a",
        bindings=_bindings(),
        agents=2,
        hub_url="http://hub",
        harnesses=HarnessRegistry(
            {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=FakeHarness(handle=_HANDLE, verdict=None))}
        ),
        workspaces=_workspaces(),
    )

    capacities = status.summary().capacities
    assert (capacities.used, capacities.agents, capacities.free) == (2, 2, 0)
    assert [(b.project, b.agents, b.used, b.free) for b in capacities.bindings] == [
        ("pr_a", 2, 1, 0),
        ("pr_b", 2, 1, 0),
    ]
