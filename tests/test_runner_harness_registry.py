"""Harness identity and exact-registry contracts (unit)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.app import create_app_for_export
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.harness_registry import (
    build_production_harness_health_probes,
    build_production_harness_registry,
)
from blizzard.runner.harness.internal.opencode_transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.process_launch import _SPAWN_EXECUTOR
from blizzard.runner.harness.registry import (
    HarnessBinding,
    HarnessRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)
from tests.runner_fakes import FakeHarness, FakeTranscriptSource


def _harness() -> FakeHarness:
    return FakeHarness(
        handle=WorkerHandle(session_id="session", pid=1, process_start_time="start", pgid=1), verdict=None
    )


@pytest.mark.unit
def test_session_reference_requires_both_owner_and_raw_session_id() -> None:
    assert SessionReference(CLAUDE_CODE_HARNESS_ID, "session") == SessionReference("claude_code", "session")

    with pytest.raises(ValueError, match="harness id"):
        SessionReference("", "session")
    with pytest.raises(ValueError, match="session id"):
        SessionReference(CLAUDE_CODE_HARNESS_ID, "")


@pytest.mark.unit
def test_registry_resolves_only_the_exact_requested_owner() -> None:
    adapter = _harness()
    source = FakeTranscriptSource()
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=source)})

    assert registry.adapter(CLAUDE_CODE_HARNESS_ID) is adapter
    assert registry.transcript_source(CLAUDE_CODE_HARNESS_ID) is source
    with pytest.raises(UnknownHarnessError) as raised:
        registry.adapter("other")
    assert raised.value.known == (CLAUDE_CODE_HARNESS_ID,)


@pytest.mark.unit
def test_registry_distinguishes_a_known_unavailable_capability() -> None:
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=_harness())})

    with pytest.raises(UnavailableHarnessError) as raised:
        registry.transcript_source(CLAUDE_CODE_HARNESS_ID)
    assert raised.value.capability == "transcript source"


@pytest.mark.unit
def test_export_app_has_an_empty_hermetic_harness_registry() -> None:
    app = create_app_for_export()

    assert app.state.harnesses.known_harnesses == ()


@pytest.mark.unit
def test_production_registry_shares_one_process_launcher_across_both_bindings(tmp_path: Path) -> None:
    """D4: both bindings inherit ONE runner-side launcher — an identity check, not equality."""
    registry = build_production_harness_registry(RunnerConfig(root=tmp_path, db_url="sqlite://"))

    claude_launcher = vars(registry.adapter(CLAUDE_CODE_HARNESS_ID))["_launcher"]
    opencode_launcher = vars(registry.adapter(OPENCODE_HARNESS_ID))["_launcher"]
    assert claude_launcher is opencode_launcher


@pytest.mark.unit
def test_production_registry_wires_a_real_opencode_transcript_source(tmp_path: Path) -> None:
    """D9: OpenCode's binding now names a real transcript source on both the adapter and
    the binding — no longer the ``UnavailableHarnessError`` an unset binding used to raise."""
    registry = build_production_harness_registry(RunnerConfig(root=tmp_path, db_url="sqlite://"))

    source = registry.transcript_source(OPENCODE_HARNESS_ID)
    assert isinstance(source, OpenCodeTranscriptSource)
    adapter_source = vars(registry.adapter(OPENCODE_HARNESS_ID))["_transcript_source"]
    assert adapter_source is source


@pytest.mark.unit
def test_production_registry_injects_its_own_executor_not_the_module_default(tmp_path: Path) -> None:
    """`bzh:dependency-injection`: the one production composition root builds and injects
    its own long-lived executor explicitly, rather than falling back to
    ``ProcessLauncher``'s module-level default — that default backs tests only."""
    registry = build_production_harness_registry(RunnerConfig(root=tmp_path, db_url="sqlite://"))

    launcher = vars(registry.adapter(CLAUDE_CODE_HARNESS_ID))["_launcher"]
    assert vars(launcher)["_executor"] is not _SPAWN_EXECUTOR


@pytest.mark.unit
def test_production_health_probes_honor_the_configured_credentials_path_override(tmp_path: Path) -> None:
    """A runner using the documented per-subscription credentials override
    (``external_usage_credentials_path``) must not silently fall back to
    ``~/.claude/.credentials.json`` for its Claude Code health probe too (blizzard#438)."""
    override = str(tmp_path / "custom-credentials.json")
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", external_usage_credentials_path=override)

    probes = build_production_harness_health_probes(config)

    probe = probes[CLAUDE_CODE_HARNESS_ID]
    assert isinstance(probe, ClaudeCodeHealthProbe)
    assert str(vars(probe)["_credentials_path"]) == override
