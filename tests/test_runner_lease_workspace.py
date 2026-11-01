"""A lease reads its workspace through its own binding row, never through the runner's first workspace.

The runner here binds two projects to two workspaces on different roots, ``ws_a`` first — the one it is addressed by.
A lease whose binding names ``ws_b`` still spawns in ``ws_b``'s root under ``ws_b``'s standing prompt, has its
judgement, dormant resume, usage range and transcript read there, and gives its environment back to ``ws_b``'s
provider. Driven against a real (tmp sqlite) runner store with fakes only at the seams."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.client import BindingDeclaration, HubQuestion, QueueEntry
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import Advance, Fill
from blizzard.runner.transcripts.service import TranscriptService
from blizzard.runner.usage.recorder import UsageRecorder
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_WORKDIR_B = "/root-b/e1"


class _Host:
    """Two workspaces on different roots, each naming its own ``e1``."""

    def __init__(self) -> None:
        self.provider_a = FakeProvider({"e1": "/root-a/e1"})
        self.provider_b = FakeProvider({"e1": _WORKDIR_B})
        self.workspaces = WorkspaceRegistry(
            [
                WorkspaceEntry("ws_a", self.provider_a, spawn_root="/root-a", prompt="A-POLICY"),
                WorkspaceEntry("ws_b", self.provider_b, spawn_root="/root-b", prompt="B-POLICY"),
            ]
        )


def _config() -> LoopConfig:
    return LoopConfig(
        runner_name="r1",
        runner_id=FIXTURE_RUNNER_ID,
        bindings=(BindingDeclaration("pr_a", "ws_a", 1), BindingDeclaration("pr_b", "ws_b", 1)),
        agents=2,
    )


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _ctx(store: SqlAlchemyRunnerStore, host: _Host, harness: FakeHarness, hub: FakeHub | None = None):
    return make_context(
        store,
        hub=hub or FakeHub(),
        provider=FakeProvider({}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_config(),
        workspaces=host.workspaces,
    )


def _bind_in_b(store: SqlAlchemyRunnerStore, chunk_id: str = "ch_1") -> None:
    store.record_binding(chunk_id=chunk_id, environment_id="e1", workdir=_WORKDIR_B, bound_at=_NOW, workspace_id="ws_b")


def _seed_lease(store: SqlAlchemyRunnerStore) -> None:
    """A build lease spawned into ``ws_b``'s ``e1``."""
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    _bind_in_b(store)


def test_a_lease_bound_in_the_second_workspace_spawns_in_its_root_under_its_prompt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _bind_in_b(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _ctx(store, _Host(), harness)

    Spawner(ctx).spawn(
        "ch_1",
        make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES),
        [AcquiredEnvironment(environment_id="e1", workdir=_WORKDIR_B)],
        via="test",
    )

    (_, preamble) = harness.spawns[0]
    assert preamble.workspace_root == "/root-b"
    assert "B-POLICY" in preamble.prompt_prefix and "A-POLICY" not in preamble.prompt_prefix
    (segment,) = store.transcript_segments_for_chunk("ch_1")
    assert segment.spawn_cwd == "/root-b"


def test_a_workspace_prompt_override_applies_only_to_leases_bound_in_that_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_workspace_prompt("ws_a", prompt="A-OVERRIDE", at=_NOW)
    store.set_workspace_prompt("ws_b", prompt="B-OVERRIDE", at=_NOW)
    _bind_in_b(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")

    Spawner(_ctx(store, _Host(), harness)).spawn(
        "ch_1",
        make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES),
        [AcquiredEnvironment(environment_id="e1", workdir=_WORKDIR_B)],
        via="test",
    )

    (_, preamble) = harness.spawns[0]
    assert "B-OVERRIDE" in preamble.prompt_prefix and "A-OVERRIDE" not in preamble.prompt_prefix


def test_a_row_naming_no_workspace_spawns_in_its_workdir_with_no_workspace_prompt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir=_WORKDIR_B, bound_at=_NOW)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")

    Spawner(_ctx(store, _Host(), harness)).spawn(
        "ch_1",
        make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES),
        [AcquiredEnvironment(environment_id="e1", workdir=_WORKDIR_B)],
        via="test",
    )

    (_, preamble) = harness.spawns[0]
    assert preamble.workspace_root == ""
    assert "POLICY" not in preamble.prompt_prefix
    (segment,) = store.transcript_segments_for_chunk("ch_1")
    assert segment.spawn_cwd == _WORKDIR_B


def test_fill_claims_a_projects_chunk_into_its_bindings_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host()
    hub = FakeHub()
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0, project_id="pr_a")]
    hub.claim_outcome = claimed_outcome("ch_1", envelope)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")

    Fill(_ctx(store, host, harness, hub)).run()

    (claim,) = hub.claims
    assert claim.workspace_id == "ws_a"
    (binding,) = store.held_bindings()
    assert (binding.workspace_id, binding.workdir) == ("ws_a", "/root-a/e1")
    (_, preamble) = harness.spawns[0]
    assert preamble.workspace_root == "/root-a" and "A-POLICY" in preamble.prompt_prefix


def test_judgement_runs_in_the_spawn_cwd_of_the_leases_own_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")

    Advance(_ctx(store, _Host(), harness, hub)).run()  # launches the detached elicitation

    assert [judged[0] for judged in harness.judged] == ["/root-b"]


def test_a_dormant_session_resumes_in_the_spawn_cwd_of_the_leases_own_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_lease(store)
    session = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a")
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session=session,
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    hub = FakeHub()
    hub.questions["qn_1"] = HubQuestion(
        question_id="qn_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Q",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321

    Advance(_ctx(store, _Host(), harness, hub)).run()

    assert [resumed[0] for resumed in harness.resumed] == ["/root-b"]


def test_a_usage_range_is_read_in_the_spawn_cwd_of_the_leases_own_workspace(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_1.1.stdout").write_text("")
    source = FakeTranscriptSource(lines_by_session={"sess-a": ["a transcript line"]})
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage=None, transcript_source=source)
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(_NOW),
        worker_files=WorkerStdoutFiles(str(stdout_dir), store),
        workspaces=_Host().workspaces,
        harnesses=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)}),
        invocation_boundaries=store,
        transcripts_wired=True,
    )
    lease = store.active_lease("lease_1")
    assert lease is not None

    recorder.record_worker(lease, store.bindings_for_chunk("ch_1"))

    assert source.read_raw_lines_cwds == ["/root-b"]


def test_a_closed_leases_transcript_is_looked_up_in_its_own_workspace(tmp_path: Path) -> None:
    """After release the held set is empty; the newest binding still names the lease's workspace."""
    store = _store(tmp_path)
    host = _Host()
    _seed_lease(store)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    service = TranscriptService(
        leases=store,
        transcript_ledger=store,
        environments=store,
        transcripts=None,  # type: ignore[arg-type]  # the spawn-cwd read reaches no repository
        archived=None,  # type: ignore[arg-type]
        workspaces=host.workspaces,
    )

    assert store.bindings_for_chunk("ch_1") == []
    assert service.spawn_cwd("ch_1") == "/root-b"


def test_a_release_hands_the_environment_back_to_its_own_workspaces_provider(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host()
    _bind_in_b(store)
    ctx = _ctx(store, host, FakeHarness(handle=_HANDLE, verdict="pass"))

    ctx.env_release.release_chunk("ch_1")

    assert (host.provider_a.released, host.provider_b.released) == ([], ["e1"])
    assert store.held_bindings() == []
