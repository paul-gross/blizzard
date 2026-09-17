"""The one neutral composition point over every coding harness the runner ships (D9).

Claude Code's own construction stays here — this module's one approved wiring site,
symmetric with OpenCode's own factory (`opencode_registry.build_opencode_binding`), so
neither adapter's concrete class escapes its approved module (`tests/test_layering.py`);
every root (`app.py`, `loop/build.py`) reaches both only through this one function."""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.internal.opencode_registry import build_opencode_binding
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe


def build_production_harness_registry(config: RunnerConfig) -> HarnessRegistry:
    """Build every configured coding-harness binding once for one composition graph."""
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    transcript_source = ClaudeCodeTranscriptSource(
        projects_root, TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    adapter = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        env_passthrough=config.worker_env_passthrough,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        transcript_source=transcript_source,
        process=LinuxProcessProbe(),
    )
    return HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=transcript_source),
            OPENCODE_HARNESS_ID: build_opencode_binding(config),
        }
    )
