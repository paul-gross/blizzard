"""A basic-provider runner's hosted app and loop agree on a worker's spawn cwd.

Basic workers spawn in their chunk environment's own workdir, so every later operation
on the session — the takeover command, the escalation's resume command, the transcript
lookup — must resolve to that workdir too, never the shared workspace root."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import build_runner_process
from blizzard.runner.config import DEFAULT_WORKSPACE_ID, RunnerConfig
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop_wiring import LoopWiring
from tests.host_files import one_workspace
from tests.runner_client import runner_client, runner_seams
from tests.runner_fakes import FIXTURE_RUNNER_ID, FakeHub, make_store, migrated_store_at

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


@pytest.mark.component
def test_basic_takeover_escalation_and_transcript_resolve_to_the_environment_workdir(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        workspaces=one_workspace(
            provider="basic", root="scratch", repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),), max_environments=3
        ),
    )
    workdir = str(tmp_path / "scratch" / "e1")
    migrated_store_at(config.db_url)
    # The process graph binds to the stored identity at build — a registered runner's store.
    store = make_store(config.db_url)
    store.record_runner_identity(RunnerIdentity(FIXTURE_RUNNER_ID, "runner-local", _NOW))
    graph = build_runner_process(config, events=EventBroker(), runner_id=FIXTURE_RUNNER_ID)
    hosted = build_hosted_app(config, process_graph=graph)
    try:
        ctx = LoopWiring(config, "", graph.host.events).context(FakeHub(), graph)
        try:
            for lease_id, chunk_id in (("lease_1", "ch_1"), ("lease_2", "ch_2")):
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
                    session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{chunk_id}"),
                    spawned_at=_NOW,
                )
            for chunk_id, environment_id in (("ch_1", "e1"), ("ch_2", "e2")):
                store.record_binding(
                    chunk_id=chunk_id,
                    environment_id=environment_id,
                    workdir=workdir,
                    bound_at=_NOW,
                    workspace_id=DEFAULT_WORKSPACE_ID,
                )
            store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
            store.record_closure(
                lease_id="lease_2", chunk_id="ch_2", node_id="nd_build", reason="escalated", closed_at=_NOW
            )

            # The directory the loop spawns a worker in for this environment.
            loop_cwd = SpawnCwd.of_session(ctx.workspaces.spawn_root(DEFAULT_WORKSPACE_ID), workdir)
            assert loop_cwd == workdir

            with runner_client(hosted.app) as client:
                takeover = client.post("/api/chunks/ch_1/takeovers", json={})
                escalations = client.get("/api/escalations")
            assert takeover.status_code == 201, takeover.text
            assert takeover.json()["command"].startswith(f"cd {workdir} && ")
            (escalation,) = escalations.json()["items"]
            assert escalation["resume_command"].startswith(f"cd {workdir} && ")

            assert runner_seams(hosted.app).transcripts._spawn_cwd("ch_1") == workdir
        finally:
            ctx.usage_http_client.close()
    finally:
        hosted.close()
        graph.close()
