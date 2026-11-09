"""Harness identity and exact-registry contracts (unit)."""

from __future__ import annotations

from concurrent.futures import Executor
from pathlib import Path

from blizzard.foundation.clock import ManualMonotonicClock, SystemClock
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from blizzard.runner.harness.wiring import build_production_harnesses, first_bindings
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import CHILDREN
from tests.support.runner_fakes import NO_TRACING


def test_the_production_opencode_probe_admits_against_the_injected_corpus(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """The probe holds the very corpus its caller built — the one the health cache reads too — not one of its own."""
    corpus = CommittedCorpus()
    _, probes = build_production_harnesses(
        RunnerConfig(root=tmp_path, db_url="sqlite://").harness_settings(NO_TRACING),
        selection=first_bindings(),
        executor=spawn_executor,
        process=LinuxProcessProbe(),
        children=CHILDREN,
        clock=SystemClock(),
        monotonic_clock=ManualMonotonicClock(),
        spawn_roots=("",),
        corpus=corpus,
    )
    probe = probes[OPENCODE_HARNESS_ID]
    assert isinstance(probe, OpenCodeHealthProbe)
    assert vars(probe)["_corpus"] is corpus
