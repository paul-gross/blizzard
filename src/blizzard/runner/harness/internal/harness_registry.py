"""The one neutral composition point over every coding harness the runner ships (D9).

Claude Code's own construction stays here — this module's one approved wiring site,
symmetric with OpenCode's own factory (`opencode_registry.build_opencode_binding`), so
neither adapter's concrete class escapes its approved module (`tests/test_layering.py`);
every root (`app.py`, `loop/build.py`) reaches both only through this one function."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

_LAUNCH_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")


def build_production_harness_registry(config: RunnerConfig) -> HarnessRegistry:
    """Build every configured harness binding once for one graph, over one shared probe/
    launcher pair (D4) — the launcher's `_LAUNCH_EXECUTOR` is module-level and shared
    across every call, never rebuilt per call: a deferred-disarm child's `PR_SET_PDEATHSIG`
    parent is the thread that forked it, so a throwaway executor's teardown can kill a
    just-launched, not-yet-scheduled child before it ever reads its confirm byte (F1)."""
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    transcript_source = ClaudeCodeTranscriptSource(
        projects_root, TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    process = LinuxProcessProbe()
    # Built and injected here (`bzh:dependency-injection`), not `ProcessLauncher`'s own
    # module-level default — this composition root is the one place that belongs (D4).
    launcher = ProcessLauncher(process, executor=_LAUNCH_EXECUTOR)
    adapter = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        env_passthrough=config.worker_env_passthrough,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        transcript_source=transcript_source,
        process=process,
        launcher=launcher,
    )
    return HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=transcript_source),
            OPENCODE_HARNESS_ID: build_opencode_binding(config, process=process, launcher=launcher),
        }
    )


def build_production_harness_health_probes(config: RunnerConfig) -> dict[str, IHarnessHealthProbe]:
    """Every configured harness binding's own :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe` (blizzard#438), this module's own approved wiring site for the
    health-probe seam, symmetric with :func:`build_production_harness_registry`'s own
    adapter construction — the composition root reaches both only through this module."""
    return {
        CLAUDE_CODE_HARNESS_ID: ClaudeCodeHealthProbe(
            binary=config.harness_binary, credentials_path=config.external_usage_credentials_path
        ),
        OPENCODE_HARNESS_ID: OpenCodeHealthProbe(binary=config.opencode_binary),
    }
