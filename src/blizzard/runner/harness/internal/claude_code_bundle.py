"""Claude Code's slice of the operator harness-config bundle.

Claude Code resolves relative paths against the worker's cwd, not the file that names them,
so no entry point declares a companion extractor."""

from __future__ import annotations

from blizzard.runner.harness.bundle import EntryPoint, HarnessLayout

CLAUDE_CODE_BUNDLE_LAYOUT = HarnessLayout(
    dirname="claude-code",
    entry_points=(
        EntryPoint("settings.json"),
        EntryPoint("mcp.json"),
        EntryPoint("agents.json"),
        EntryPoint("plugins", is_dir=True),
    ),
)
