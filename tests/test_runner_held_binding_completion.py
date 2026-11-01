"""Held bindings named by workspace and project — the completion step ahead of FILL, and FILL's own writes.

A held row written before ``env_bindings`` carried ``workspace_id``/``project_id`` is completed every tick
until it is whole: its workspace from a workdir match against the provider's listed environments, its project
from the chunk-status batch read. A row matching no workspace keeps its lease and its agent slot. Driven
against a real (tmp sqlite) runner store with fakes only at the seams."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.hub.chunk_status_cache import MemoizingChunkViewCache
from blizzard.runner.hub.client import QueueEntry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import CompleteHeldBindings, Fill
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.runner_fakes import (
    FIXTURE_PROJECT_ID,
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
    serving,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _ctx(store, hub, *, pool: dict[str, str] | None = None, agents: int = 1):
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider(pool if pool is not None else {"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=LoopConfig(runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(agents), agents=agents),
    )
    # The tick's own memoizing cache, so a test sees the hub calls one tick makes.
    return dataclasses.replace(ctx, chunk_views=MemoizingChunkViewCache(ctx.hub))


def _held_rows(store: SqlAlchemyRunnerStore) -> dict[str, tuple[str | None, str | None]]:
    return {b.environment_id: (b.workspace_id, b.project_id) for b in store.held_bindings()}


def _seed_pre_revision_binding(store, chunk_id: str, environment_id: str, workdir: str) -> None:
    """A held row as the previous release wrote it — no workspace, no project."""
    store.record_binding(chunk_id=chunk_id, environment_id=environment_id, workdir=workdir, bound_at=_NOW)


def _seed_running_lease(store, chunk_id: str) -> None:
    lease_id = f"lease_{chunk_id}"
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease_id,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )


def test_a_pre_revision_held_row_takes_the_workspace_its_workdir_resolves_to(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    _seed_pre_revision_binding(store, "ch_1", "e1", "/ws/./e1/")  # the listed /ws/e1, spelled differently
    # The same environment name under another root: never matched by name alone.
    _seed_pre_revision_binding(store, "ch_2", "e2", "/elsewhere/e2")

    CompleteHeldBindings(_ctx(store, hub)).run()

    rows = _held_rows(store)
    assert rows["e1"][0] == "ws1"
    assert rows["e2"][0] is None


def test_a_held_row_takes_whichever_host_workspace_lists_its_workdir(tmp_path: Path) -> None:
    """Two workspaces on different roots both name ``e1``: each row takes the workspace whose root holds it."""
    store = _store(tmp_path)
    hub = FakeHub()
    _seed_pre_revision_binding(store, "ch_a", "e1", "/root-a/e1")
    _seed_pre_revision_binding(store, "ch_b", "e1", "/root-b/e1")
    workspaces = WorkspaceRegistry(
        [
            WorkspaceEntry("ws_a", FakeProvider({"e1": "/root-a/e1"}), spawn_root=""),
            WorkspaceEntry("ws_b", FakeProvider({"e1": "/root-b/e1"}), spawn_root=""),
        ]
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        workspaces=workspaces,
    )

    CompleteHeldBindings(ctx).run()

    assert {b.chunk_id: b.workspace_id for b in store.held_bindings()} == {"ch_a": "ws_a", "ch_b": "ws_b"}


def test_a_held_row_takes_its_project_from_one_batch_status_read(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(chunk_id="ch_1", status=ChunkStatus.RUNNING, project_id="pr_1")
    hub.chunks["ch_2"] = ChunkState(chunk_id="ch_2", status=ChunkStatus.RUNNING, project_id="pr_2")
    _seed_pre_revision_binding(store, "ch_1", "e1", "/ws/e1")
    _seed_pre_revision_binding(store, "ch_2", "e2", "/ws/e2")

    CompleteHeldBindings(_ctx(store, hub)).run()

    assert _held_rows(store) == {"e1": ("ws1", "pr_1"), "e2": ("ws1", "pr_2")}
    assert len(hub.chunk_statuses_calls) == 1
    assert sorted(hub.chunk_statuses_calls[0]) == ["ch_1", "ch_2"]


def test_an_unanswered_row_stays_empty_and_is_retried_next_tick(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    hub.down = True
    _seed_pre_revision_binding(store, "ch_1", "e1", "/ws/e1")

    CompleteHeldBindings(_ctx(store, hub)).run()
    assert _held_rows(store) == {"e1": ("ws1", None)}  # the workspace needs no hub; the project waits

    hub.down = False
    hub.chunks["ch_1"] = ChunkState(chunk_id="ch_1", status=ChunkStatus.RUNNING, project_id="pr_1")
    CompleteHeldBindings(_ctx(store, hub)).run()
    assert _held_rows(store) == {"e1": ("ws1", "pr_1")}


def test_completion_does_nothing_once_every_held_row_is_whole(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    store.record_binding(
        chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1", project_id="pr_1"
    )

    CompleteHeldBindings(_ctx(store, hub)).run()

    assert hub.chunk_statuses_calls == []
    assert _held_rows(store) == {"e1": ("ws1", "pr_1")}


def test_a_held_rows_completion_never_overwrites_a_column_already_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(chunk_id="ch_1", status=ChunkStatus.RUNNING, project_id="pr_new")
    store.record_binding(
        chunk_id="ch_1", environment_id="e1", workdir="/elsewhere/e1", bound_at=_NOW, workspace_id="ws_old"
    )

    CompleteHeldBindings(_ctx(store, hub)).run()

    assert _held_rows(store) == {"e1": ("ws_old", "pr_new")}


def test_with_an_unmatched_held_row_fill_never_exceeds_the_bindings_agents(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    _seed_pre_revision_binding(store, "ch_old", "e9", "/elsewhere/e9")
    _seed_running_lease(store, "ch_old")
    hub.chunks["ch_old"] = ChunkState(
        chunk_id="ch_old", status=ChunkStatus.RUNNING, route_runner_id=hub.default_runner_id
    )
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0, project_id="pr_1")]
    hub.claim_outcome = claimed_outcome("ch_1", make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES))
    ctx = _ctx(store, hub, agents=1)

    CompleteHeldBindings(ctx).run()
    Fill(ctx).run()

    assert _held_rows(store)["e9"][0] is None  # still unmatched, still held
    assert hub.claims == []
    assert [lease.chunk_id for lease in store.list_active_leases()] == ["ch_old"]


def test_a_new_binding_carries_the_workspace_and_the_entrys_project(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0, project_id=FIXTURE_PROJECT_ID)]
    hub.claim_outcome = claimed_outcome("ch_1", make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES))

    Fill(_ctx(store, hub)).run()

    assert len(hub.claims) == 1
    assert _held_rows(store) == {"e1": ("ws1", FIXTURE_PROJECT_ID)}


def test_an_entry_naming_no_project_is_never_claimed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    hub.narrows_peek = False  # the entry reaches the runner as the hub sent it
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]

    Fill(_ctx(store, hub)).run()

    assert hub.claims == [] and _held_rows(store) == {}
