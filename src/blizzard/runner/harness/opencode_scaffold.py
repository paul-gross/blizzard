"""The harness package's public entry point for scaffolding OpenCode's runtime-root files
(execution spec, "Runner-owned plugin", D7).

``Runtime.init`` is not a composition root, so it may not reach into ``harness/internal/``
directly (``bzh:dependency-injection``); it calls this harness-neutral surface instead,
which delegates to the real scaffolding under ``harness/internal/``."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.internal.opencode_plugin import (
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    plugin_reference,
    write_plugin,
)
from blizzard.runner.harness.internal.opencode_worker_config import write_worker_config


def scaffold_opencode_worker_config(root: Path, worker_config_path: Path) -> None:
    """Write the runner-owned OpenCode plugin under ``root`` and the permission/plugin
    document at ``worker_config_path`` referencing it — idempotently, exactly as ``init``
    scaffolds every other runtime-root file."""
    plugin_path = root / PLUGIN_DIRNAME / PLUGIN_FILENAME
    write_plugin(plugin_path)
    write_worker_config(worker_config_path, plugins=(plugin_reference(plugin_path),))


__all__ = ["scaffold_opencode_worker_config"]
