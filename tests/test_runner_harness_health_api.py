"""``GET /api/harness-health`` (component tier) — the runner's own harness
health diagnostics, proven end-to-end: real route wiring over a real (if unreachable, for
determinism) harness binary, and an injected cache proving degradations surface too.

The route itself never probes — it only reads the last result some
earlier ``refresh()`` computed, the way the loop's own tick populates the cache it shares
with the served app (``HostedApp.harness_health``) — so every test here calls ``refresh()``
on the injected cache itself before reading the route, standing in for that tick."""

from __future__ import annotations

from concurrent.futures import Executor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.claude_code.health import (
    ADMITTED_CLAUDE_CODE_RANGE_DISPLAY,
    ClaudeCodeHealthProbe,
)
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.adapter import OpenCodeAdapter
from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE_DISPLAY
from blizzard.runner.harness.opencode.health import OpenCodeHealthProbe
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.harness_sections import claude_code, opencode, sections

pytestmark = pytest.mark.component


class _HealthyWithDegradationProbe:
    """A stand-in health probe reporting a healthy, authenticated binary with one declared
    degradation — deterministic, with no dependency on this machine's own credential
    files or a real binary on ``PATH``."""

    def binary_present(self) -> bool:
        return True

    def config_conflicts(self) -> tuple[str, ...]:
        return ()

    def probe_authentication(self) -> bool:
        return True

    def supported_version(self) -> None:
        return None

    def supported_version_display(self) -> None:
        return None

    def normalize_version(self, raw: str | None) -> str | None:
        return raw

    def classifies_offline(self) -> bool:
        return False

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return (DeclaredDegradation(probe=CompatibilityProbe.USAGE_COST, summary="no cost figure on some turns"),)


def test_reports_missing_binary_for_an_unresolvable_configured_path(tmp_path: Path, spawn_executor: Executor) -> None:
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        harness_sections=sections(ClaudeCodeSection(binary=str(tmp_path / "no-such-claude-binary"))),
    )
    probe = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        binary=claude_code(config).binary,
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor),
    )
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    health = HarnessHealthCache(
        corpus=CommittedCorpus(),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={CLAUDE_CODE_HARNESS_ID: ClaudeCodeHealthProbe(binary=claude_code(config).binary)},
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=adapter, observed_version=adapter.observe_version())
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["harness_id"] == CLAUDE_CODE_HARNESS_ID
    assert items[0]["available"] is False
    assert items[0]["cause"] == "missing_binary"


def test_reports_available_with_a_declared_degradation(tmp_path: Path, spawn_executor: Executor) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    probe = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        binary=claude_code(config).binary,
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor),
    )
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    health = HarnessHealthCache(
        corpus=CommittedCorpus(),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={CLAUDE_CODE_HARNESS_ID: _HealthyWithDegradationProbe()},
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=adapter, observed_version=None)
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["available"] is True
    assert items[0]["cause"] == "declared_degradation"
    assert items[0]["degradations"] == ["no cost figure on some turns"]


def test_a_misconfigured_opencode_corpus_degrades_only_opencode(tmp_path: Path, spawn_executor: Executor) -> None:
    """With an empty corpus root and an unresolvable OpenCode binary, the route answers 200
    with OpenCode unavailable (`missing_binary`) and Claude Code still available. The
    unresolvable binary path keeps the test independent of the machine's `PATH`."""
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        harness_sections=sections(OpenCodeSection(binary=str(tmp_path / "no-such-opencode-binary"))),
    )
    process = LinuxProcessProbe()
    launcher = ProcessLauncher(process, executor=spawn_executor)
    claude_adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()), binary=claude_code(config).binary, process=process, launcher=launcher
    )
    opencode_adapter = OpenCodeAdapter(
        worker_env=AllowlistedEnv.of(()), binary=opencode(config).binary, process=process, launcher=launcher
    )
    harnesses = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude_adapter),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode_adapter),
        }
    )
    # `corpus=CommittedCorpus(tmp_path)` is empty: no manifest to read.
    health = HarnessHealthCache(
        corpus=CommittedCorpus(),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={
            CLAUDE_CODE_HARNESS_ID: _HealthyWithDegradationProbe(),
            OPENCODE_HARNESS_ID: OpenCodeHealthProbe(
                binary=opencode(config).binary, auth_path=None, corpus=CommittedCorpus(tmp_path)
            ),
        },
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=claude_adapter, observed_version=None)
    health.refresh(OPENCODE_HARNESS_ID, adapter=opencode_adapter, observed_version="1.18.25")
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = {item["harness_id"]: item for item in resp.json()["items"]}
    assert items[CLAUDE_CODE_HARNESS_ID]["available"] is True
    assert items[OPENCODE_HARNESS_ID]["available"] is False
    assert items[OPENCODE_HARNESS_ID]["cause"] == "missing_binary"


def test_admitted_range_surfaces_per_binding(tmp_path: Path, spawn_executor: Executor) -> None:
    """``admitted_range`` is populated from each binding's own
    `supported_version()` display string — both Claude Code and OpenCode
    declare one today, each its own literal."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    process = LinuxProcessProbe()
    launcher = ProcessLauncher(process, executor=spawn_executor)
    claude_adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(()), binary=claude_code(config).binary, process=process, launcher=launcher
    )
    opencode_adapter = OpenCodeAdapter(
        worker_env=AllowlistedEnv.of(()), binary=opencode(config).binary, process=process, launcher=launcher
    )
    harnesses = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude_adapter),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode_adapter),
        }
    )
    health = HarnessHealthCache(
        corpus=CommittedCorpus(),
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={
            CLAUDE_CODE_HARNESS_ID: ClaudeCodeHealthProbe(binary=claude_code(config).binary),
            OPENCODE_HARNESS_ID: OpenCodeHealthProbe(
                binary=opencode(config).binary, auth_path=None, corpus=CommittedCorpus()
            ),
        },
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=claude_adapter, observed_version=None)
    health.refresh(OPENCODE_HARNESS_ID, adapter=opencode_adapter, observed_version=None)
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = {item["harness_id"]: item for item in resp.json()["items"]}
    assert items[CLAUDE_CODE_HARNESS_ID]["admitted_range"] == ADMITTED_CLAUDE_CODE_RANGE_DISPLAY
    # Pinned literally: `str(SpecifierSet(...))` reorders clauses to `<3.0,>=2.1`.
    assert items[CLAUDE_CODE_HARNESS_ID]["admitted_range"] == ">=2.1,<3.0"
    assert items[OPENCODE_HARNESS_ID]["admitted_range"] == ADMITTED_OPENCODE_RANGE_DISPLAY
    # Pinned literally: `str(SpecifierSet(...))` reorders clauses to `<2.0,>=1.18.25`.
    assert items[OPENCODE_HARNESS_ID]["admitted_range"] == ">=1.18.25,<2.0"
