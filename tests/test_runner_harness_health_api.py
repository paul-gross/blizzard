"""``GET /api/harness-health`` (blizzard#438, component tier) — the runner's own harness
health diagnostics, proven end-to-end: real route wiring over a real (if unreachable, for
determinism) harness binary, and an injected cache proving degradations surface too.

The route itself never probes (blizzard#438, F4) — it only reads the last result some
earlier ``refresh()`` computed, the way the loop's own tick populates the cache it shares
with the served app (``HostedApp.harness_health``) — so every test here calls ``refresh()``
on the injected cache itself before reading the route, standing in for that tick."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.compatibility import CompatibilityProbe
from blizzard.runner.harness.health import DeclaredDegradation
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from blizzard.runner.harness.internal.opencode_probe import ADMITTED_OPENCODE_VERSIONS
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.capability_snapshot import HarnessHealthCache
from blizzard.runner.loop.process import LinuxProcessProbe

pytestmark = pytest.mark.component


class _HealthyWithDegradationProbe:
    """A stand-in health probe reporting a healthy, authenticated binary with one declared
    degradation — deterministic, with no dependency on this machine's own credential
    files or a real binary on ``PATH``."""

    def binary_present(self) -> bool:
        return True

    def probe_authentication(self) -> bool:
        return True

    def supported_version(self) -> frozenset[str]:
        return frozenset()

    def declared_degradations(self) -> tuple[DeclaredDegradation, ...]:
        return (DeclaredDegradation(probe=CompatibilityProbe.USAGE_COST, summary="no cost figure on some turns"),)


def test_reports_missing_binary_for_an_unresolvable_configured_path(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", harness_binary=str(tmp_path / "no-such-claude-binary"))
    probe = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(binary=config.harness_binary, process=probe, launcher=ProcessLauncher(probe))
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    health = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={CLAUDE_CODE_HARNESS_ID: ClaudeCodeHealthProbe(binary=config.harness_binary)},
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


def test_reports_available_with_a_declared_degradation(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    probe = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(binary=config.harness_binary, process=probe, launcher=ProcessLauncher(probe))
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    health = HarnessHealthCache(
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


def test_a_misconfigured_opencode_corpus_degrades_only_opencode(tmp_path: Path) -> None:
    """A missing corpus manifest for an admitted OpenCode version degrades that binding
    alone (blizzard#438, F10) — `OpenCodeHealthProbe` construction never raises over it, and
    it neither prevents Claude Code's own entry, in the same registry and cache, from
    reporting healthy, nor the route from responding at all. An unresolvable binary path
    (mirroring `test_reports_missing_binary_for_an_unresolvable_configured_path` above) keeps
    this hermetic — no dependency on whether an `opencode` binary happens to be on this
    machine's own `PATH`; the exact cause matters less here than construction, the shared
    cache, and the route all surviving the corpus defect intact."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", opencode_binary=str(tmp_path / "no-such-opencode-binary"))
    process = LinuxProcessProbe()
    launcher = ProcessLauncher(process)
    claude_adapter = ClaudeCodeAdapter(binary=config.harness_binary, process=process, launcher=launcher)
    opencode_adapter = OpenCodeAdapter(binary=config.opencode_binary, process=process, launcher=launcher)
    harnesses = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude_adapter),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode_adapter),
        }
    )
    # `corpus_root=tmp_path` (empty) never raises out of construction (F10) — the whole
    # point being proven here.
    health = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={
            CLAUDE_CODE_HARNESS_ID: _HealthyWithDegradationProbe(),
            OPENCODE_HARNESS_ID: OpenCodeHealthProbe(binary=config.opencode_binary, corpus_root=tmp_path),
        },
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=claude_adapter, observed_version=None)
    an_admitted_version = sorted(ADMITTED_OPENCODE_VERSIONS)[0]
    health.refresh(OPENCODE_HARNESS_ID, adapter=opencode_adapter, observed_version=an_admitted_version)
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = {item["harness_id"]: item for item in resp.json()["items"]}
    assert items[CLAUDE_CODE_HARNESS_ID]["available"] is True
    assert items[OPENCODE_HARNESS_ID]["available"] is False
    assert items[OPENCODE_HARNESS_ID]["cause"] == "missing_binary"


def test_admitted_versions_surface_per_binding(tmp_path: Path) -> None:
    """``admitted_versions`` (blizzard#438, F20) is populated from each binding's own
    `supported_version()` — non-empty for OpenCode, empty for a binding (Claude Code) that
    declares no supported-version range at all."""
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    process = LinuxProcessProbe()
    launcher = ProcessLauncher(process)
    claude_adapter = ClaudeCodeAdapter(binary=config.harness_binary, process=process, launcher=launcher)
    opencode_adapter = OpenCodeAdapter(binary=config.opencode_binary, process=process, launcher=launcher)
    harnesses = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude_adapter),
            OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode_adapter),
        }
    )
    health = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        probes={
            CLAUDE_CODE_HARNESS_ID: ClaudeCodeHealthProbe(binary=config.harness_binary),
            OPENCODE_HARNESS_ID: OpenCodeHealthProbe(binary=config.opencode_binary),
        },
        selftest_results=None,
    )
    health.refresh(CLAUDE_CODE_HARNESS_ID, adapter=claude_adapter, observed_version=None)
    health.refresh(OPENCODE_HARNESS_ID, adapter=opencode_adapter, observed_version=None)
    client = TestClient(create_app(config, harnesses=harnesses, harness_health=health))

    resp = client.get("/api/harness-health")

    assert resp.status_code == 200, resp.text
    items = {item["harness_id"]: item for item in resp.json()["items"]}
    assert items[CLAUDE_CODE_HARNESS_ID]["admitted_versions"] == []
    assert set(items[OPENCODE_HARNESS_ID]["admitted_versions"]) == ADMITTED_OPENCODE_VERSIONS
