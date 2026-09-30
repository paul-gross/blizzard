"""The one neutral composition point over every coding harness the runner ships.

Claude Code's own construction stays here — this module's one approved wiring site,
symmetric with OpenCode's own factory (`opencode_registry.build_opencode_binding`), so
neither adapter's concrete class escapes its approved module (`tests/test_layering.py`);
the composition root reaches both only through this one function."""

from __future__ import annotations

from concurrent.futures import Executor
from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from blizzard.runner.harness.internal.opencode_registry import build_opencode_binding
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe


def build_production_harness_registry(
    config: RunnerConfig, *, executor: Executor, process: LinuxProcessProbe
) -> HarnessRegistry:
    """Build every enabled harness binding once for one graph, over one shared probe/
    launcher pair. The process graph owns the injected executor and probe for the
    lifetime of every child launch."""
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    transcript_source = ClaudeCodeTranscriptSource(
        projects_root, TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    launcher = ProcessLauncher(process, executor=executor)
    # Insertion order is claude_code then opencode: the first binding is the runner's default harness.
    bindings: dict[str, HarnessBinding] = {}
    if config.claude_code_enabled:
        adapter = ClaudeCodeAdapter(
            binary=config.harness_binary,
            settings_path=config.worker_settings_path,
            permission_mode=config.harness_permission_mode,
            worker_env=config.worker_env,
            model_aliases=config.model_aliases,
            effort_aliases=config.effort_aliases,
            transcript_source=transcript_source,
            process=process,
            launcher=launcher,
        )
        bindings[CLAUDE_CODE_HARNESS_ID] = HarnessBinding(adapter=adapter, transcript_source=transcript_source)
    if config.opencode_enabled:
        bindings[OPENCODE_HARNESS_ID] = build_opencode_binding(config, process=process, launcher=launcher)
    return HarnessRegistry(bindings)


def build_production_harness_health_probes(config: RunnerConfig) -> dict[str, IHarnessHealthProbe]:
    """Every enabled harness binding's own :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe`, this module's own approved wiring site for the
    health-probe seam, symmetric with :func:`build_production_harness_registry`'s own
    adapter construction — the composition root reaches both only through this module."""
    probes: dict[str, IHarnessHealthProbe] = {}
    if config.claude_code_enabled:
        probes[CLAUDE_CODE_HARNESS_ID] = ClaudeCodeHealthProbe(
            binary=config.harness_binary, credentials_path=config.claude_code_credentials_path
        )
    if config.opencode_enabled:
        probes[OPENCODE_HARNESS_ID] = OpenCodeHealthProbe(
            binary=config.opencode_binary, auth_path=config.opencode_auth_path
        )
    return probes
