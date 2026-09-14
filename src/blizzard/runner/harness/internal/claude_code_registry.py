"""Production construction for the one initially supported coding harness."""

from __future__ import annotations

from pathlib import Path

from blizzard.foundation.logging import get_logger
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe


def build_production_harness_registry(config: RunnerConfig) -> HarnessRegistry:
    """Build Claude Code's adapter and transcript source once for one composition graph."""
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
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=transcript_source)}
    )
