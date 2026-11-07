"""Harness identity and exact-registry contracts (unit)."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from blizzard.runner.harness.wiring import (
    build_production_harness_health_probes,
    first_bindings,
)
from tests.support.child_launch import CHILDREN
from tests.support.runner_fakes import NO_TRACING


def test_the_production_opencode_probe_admits_against_the_injected_corpus(tmp_path: Path) -> None:
    """The probe holds the very corpus its caller built — the one the health cache reads too — not one of its own."""
    corpus = CommittedCorpus()
    probe = build_production_harness_health_probes(
        RunnerConfig(root=tmp_path, db_url="sqlite://").harness_settings(NO_TRACING),
        selection=first_bindings(),
        spawn_roots=("",),
        corpus=corpus,
        children=CHILDREN,
    )[OPENCODE_HARNESS_ID]
    assert isinstance(probe, OpenCodeHealthProbe)
    assert vars(probe)["_corpus"] is corpus
