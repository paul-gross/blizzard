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
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
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
