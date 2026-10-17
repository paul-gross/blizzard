"""``UsageRecorder``'s transcript-sum fallback hands the adapter the observation it already made
of the same lines, distinct from the expected model — and none when it observed nothing of
them, so the adapter derives its own."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.internal.worker_stdout_files import WorkerStdoutFiles
from blizzard.runner.leases.model import Lease, NewLease
from blizzard.runner.usage.recorder import UsageRecorder
from tests.runner_fakes import (
    FakeHarness,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_store,
    single_workspace,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
_SESSION_ID = "ses_a"
_HANDLE = WorkerHandle(session_id=_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
# No workspace root is configured, so the bound environment's workdir is the spawn cwd read under.
_BINDINGS = [EnvBinding(chunk_id="ch_1", environment_id="e1", workdir="/wd/e1", bound_at=_NOW)]


def _lease(store: SqlAlchemyRunnerStore, *, resolved_model: str | None) -> Lease:
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
            resolved_model=resolved_model,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=_NOW,
    )
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
    lease = store.active_lease("lease_1")
    assert lease is not None
    return lease


def _recorder(
    tmp_path: Path, store: SqlAlchemyRunnerStore, *, stdout: str, now: datetime = _NOW
) -> tuple[UsageRecorder, FakeHarness, FakeTranscriptSource]:
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_1.1.stdout").write_text(stdout)
    source = FakeTranscriptSource(lines_by_session={_SESSION_ID: ["a transcript line"]})
    # No envelope parses (`usage=None`), so every case falls back to the transcript sum.
    harness = FakeHarness(
        handle=_HANDLE, verdict="pass", usage=None, transcript_source=source, observed_model="claude-observed"
    )
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    recorder = UsageRecorder(
        leases=store,
        usage=store,
        clock=FixedClock(now),
        worker_files=WorkerStdoutFiles(str(stdout_dir), store),
        workspaces=single_workspace(FakeProvider({})),
        harnesses=registry,
        invocation_boundaries=store,
        transcripts_wired=True,
    )
    return recorder, harness, source


def test_an_unpinned_fallback_hands_over_the_observation_already_made_of_its_lines(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    lease = _lease(store, resolved_model=None)
    recorder, harness, source = _recorder(tmp_path, store, stdout="<envelope>")

    recorder.record_worker(lease, bindings=_BINDINGS)

    assert harness.sum_observed == ["claude-observed"]
    assert harness.needs_usage_transcript_calls == [("<envelope>", None)]
    # Observed once, then reused: neither the read nor the observation repeats for the sum.
    assert harness.observed_model_calls == [("a transcript line",)]
    assert source.read_raw_lines_cwds == ["/wd/e1"]
    # The envelope parse is handed the same lines the observation was made of.
    assert harness.parse_transcript_lines == [("a transcript line",)]
    assert harness.usage_models == ["claude-observed", "claude-observed"]


def test_a_pinned_fallback_hands_over_no_observation_beside_its_expected_model(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    lease = _lease(store, resolved_model="sonnet")
    recorder, harness, source = _recorder(tmp_path, store, stdout="<envelope>")

    recorder.record_worker(lease, bindings=_BINDINGS)

    # The alias rides as the expected model only; the adapter derives what actually ran.
    assert harness.sum_observed == [None]
    assert harness.needs_usage_transcript_calls == [("<envelope>", "sonnet")]
    assert harness.usage_models == ["sonnet", "sonnet"]
    assert harness.observed_model_calls == []
    # The fallback's own fresh read is the only one, under the bound workdir.
    assert source.read_raw_lines_cwds == ["/wd/e1"]


def test_a_fallback_over_freshly_read_lines_hands_over_no_observation(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    lease = _lease(store, resolved_model=None)
    recorder, harness, source = _recorder(tmp_path, store, stdout="")

    recorder.record_worker(lease, bindings=_BINDINGS)

    # No envelope, so no observation was made before the range was read for the sum.
    assert harness.sum_observed == [None]
    assert harness.observed_model_calls == []
    assert harness.usage_models == [None]
    assert source.read_raw_lines_cwds == ["/wd/e1"]


def test_each_generation_is_windowed_from_its_own_boundary_and_capped_at_an_open_judgement(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    lease = _lease(store, resolved_model=None)
    recorder, harness, _ = _recorder(tmp_path, store, stdout="<envelope>", now=_NOW + timedelta(hours=1))
    judged_at = _NOW + timedelta(minutes=10)
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="judge",
        start_position=None,
        opened_at=judged_at,
    )

    recorder.record_worker(lease, bindings=_BINDINGS)

    resumed_at = _NOW + timedelta(minutes=20)
    store.record_spawn(
        "lease_1",
        pid=101,
        process_start_time="start-101",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION_ID),
        spawned_at=resumed_at,
    )
    store.record_boundary_open(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=2,
        kind="resume",
        start_position=None,
        opened_at=resumed_at,
    )
    (tmp_path / "stdout" / "lease_1.2.stdout").write_text("<envelope>")
    resumed = store.active_lease("lease_1")
    assert resumed is not None

    recorder.record_worker(resumed, bindings=_BINDINGS)

    # Generation 1 ends where its judgement opened; generation 2, unjudged, runs to now.
    expected = [(_NOW, judged_at), (resumed_at, _NOW + timedelta(hours=1))]
    assert harness.parse_windows == expected
    assert harness.sum_windows == expected
