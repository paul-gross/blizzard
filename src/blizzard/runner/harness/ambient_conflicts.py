"""The harness package's public entry point for ambient-settings conflict detection.

``cli/`` is not a composition root for ``harness/internal/`` (``bzh:internal-visibility``); it
and the registry take the Claude Code check through this surface, which delegates to
``harness/internal/claude_code_ambient.py``."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.claude_code_ambient import (
    MANAGED_SETTINGS_PATHS,
    AmbientSources,
    detect_ambient_conflicts,
    user_config_dir,
)
from blizzard.runner.harness.internal.claude_code_settings_compose import resolved_permission_mode
from blizzard.runner.harness.spawn_cwd import SpawnCwd


def claude_code_ambient_sources(config: RunnerConfig) -> AmbientSources:
    """The settings locations a Claude Code worker spawned by ``config`` would load: the managed
    path(s), the user config dir its allowlisted env resolves (never the daemon's own), and the
    spawn cwd."""
    project = SpawnCwd(config.workspace_root, None).path
    return AmbientSources(
        managed_paths=MANAGED_SETTINGS_PATHS,
        user_dir=user_config_dir(config.worker_env.variables),
        project_dir=Path(project) if project else None,
    )


def claude_code_permission_mode(config: RunnerConfig) -> str | None:
    """The ``--permission-mode`` value the Claude Code adapter passes under ``config``."""
    return resolved_permission_mode(config.autonomy, config.harness_permission_mode)


def claude_code_config_conflicts(config: RunnerConfig) -> tuple[str, ...]:
    """Each ambient setting that defeats the runner's wiring, rendered as its file and key."""
    found = detect_ambient_conflicts(claude_code_ambient_sources(config), claude_code_permission_mode(config))
    return tuple(str(conflict) for conflict in found)
