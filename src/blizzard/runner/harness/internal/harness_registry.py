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
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.internal.opencode_registry import build_opencode_binding
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe

# One executor for the whole process, not one per registry build (F1): a deferred-disarm
# child's `PR_SET_PDEATHSIG` parent is the specific OS thread that forked it, not this
# process — a callable-once composition root (the `tick` CLI, and every `LoopWiring.of`
# call the steppable-loop tests make, `bzh:steppable-loop`) drops its `HarnessRegistry`,
# and with it a per-call executor, the instant the call returns. That races the trampoline:
# if its worker thread exits before the newly-forked trampoline is even scheduled, the
# kernel delivers the death signal before the child ever reads its confirm byte, so it is
# killed unexecuted — never reaching the real binary. Module-level and built once, this
# executor's thread outlives every individual registry build, so a confirmed child is
# never raced by its own launcher's teardown.
_LAUNCH_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")


def build_production_harness_registry(config: RunnerConfig) -> HarnessRegistry:
    """Build every configured harness binding once for one graph, over one shared probe/launcher pair (D4)."""
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    transcript_source = ClaudeCodeTranscriptSource(
        projects_root, TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    process = LinuxProcessProbe()
    # Built and injected here (`bzh:dependency-injection`), not `ProcessLauncher`'s own
    # module-level default — this composition root is the one place that belongs (D4).
    # Shared across every call (see `_LAUNCH_EXECUTOR`), not rebuilt per call.
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
