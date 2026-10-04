"""The durable selftest-result repository and its wiring into ``SelfTestService``
— round-tripping a recorded run, latest-wins-per-harness, and a fresh
read through the same store seeing a completed run's outcome (the "survives a
restart" property, without an actual process restart)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.selftest_result import IWriteSelfTestResultRepository, LatestSelfTestResult
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from blizzard.runner.selftest.service import SelfTestService
from tests.runner_fakes import make_store
from tests.test_runner_selftest import _FixedPidAdapter, _RecordingProcessProbe

pytestmark = pytest.mark.component


def test_recording_a_result_and_reading_it_back_round_trips_it(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    assert store.latest_selftest_result("claude_code") is None

    store.record_selftest_result(harness_id="claude_code", status="passed", error=None, recorded_at=clock.now())

    result = store.latest_selftest_result("claude_code")
    assert result is not None
    assert result.harness_id == "claude_code"
    assert result.status == "passed"
    assert result.error is None


def test_a_later_result_for_the_same_harness_supersedes_the_earlier_one(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    store.record_selftest_result(harness_id="claude_code", status="failed", error="boom", recorded_at=clock.now())
    clock.advance(timedelta(seconds=1))
    store.record_selftest_result(harness_id="claude_code", status="passed", error=None, recorded_at=clock.now())

    result = store.latest_selftest_result("claude_code")
    assert result is not None
    assert result.status == "passed"
    assert result.error is None


def test_a_different_harness_is_never_confused_with_another_ones_result(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(instant=datetime(2026, 1, 1, tzinfo=UTC))

    store.record_selftest_result(harness_id="claude_code", status="passed", error=None, recorded_at=clock.now())

    assert store.latest_selftest_result("opencode") is None


class _SlowWriter:
    """Delegates to a real store, holding each write back long enough that a reader
    polling the run's status would see it go terminal first under the wrong ordering."""

    def __init__(self, store: IWriteSelfTestResultRepository) -> None:
        self._store = store

    def latest_selftest_result(self, harness_id: str) -> LatestSelfTestResult | None:
        return self._store.latest_selftest_result(harness_id)

    def record_selftest_result(self, *, harness_id: str, status: str, error: str | None, recorded_at: datetime) -> None:
        time.sleep(0.3)
        self._store.record_selftest_result(harness_id=harness_id, status=status, error=error, recorded_at=recorded_at)


def test_selftest_service_persists_a_completed_runs_outcome_through_a_wired_repository(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    clock = FixedClock(instant=datetime(2026, 1, 1, tzinfo=UTC))
    adapter = _FixedPidAdapter(spawn_pid=111, resume_pid=222)
    service = SelfTestService(
        harnesses=HarnessRegistry({"claude_code": HarnessBinding(adapter=adapter)}),
        scratch_git=SubprocessScratchGit(),
        process=_RecordingProcessProbe(),
        clock=clock,
        results=_SlowWriter(store),
    )

    run = service.start("claude_code")
    deadline = time.monotonic() + 10.0
    finished = service.get(run.id)
    while finished is not None and finished.status == "running":
        if time.monotonic() > deadline:
            raise AssertionError("selftest run never finished")
        time.sleep(0.02)
        finished = service.get(run.id)
    assert finished is not None

    recorded = store.latest_selftest_result("claude_code")
    assert recorded is not None
    assert recorded.status == finished.status
