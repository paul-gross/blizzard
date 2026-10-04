"""The harness package's public entry point for ambient-settings conflict detection.

The Claude Code declaration's probe and ``harness status`` diagnostics take the check through
this surface, which delegates to ``harness/claude_code/ambient.py``."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.claude_code.ambient import (
    MANAGED_SETTINGS_PATHS,
    AmbientSources,
    detect_ambient_conflicts,
    user_config_dir,
)
from blizzard.runner.harness.claude_code.settings_compose import resolved_permission_mode
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.spawn_cwd import SpawnCwd


def claude_code_ambient_sources(worker_env: AllowlistedEnv, *, spawn_root: str) -> AmbientSources:
    """The settings locations a Claude Code worker would load: the managed path(s), the user
    config dir its allowlisted ``worker_env`` resolves (never the daemon's own), and the spawn
    cwd — ``spawn_root`` as the workspace provider answers it."""
    project = SpawnCwd(spawn_root, None).path
    return AmbientSources(
        managed_paths=MANAGED_SETTINGS_PATHS,
        user_dir=user_config_dir(worker_env.variables),
        project_dir=Path(project) if project else None,
    )


def claude_code_permission_mode(autonomy: Autonomy, override: str | None) -> str | None:
    """The ``--permission-mode`` value the Claude Code adapter passes under ``autonomy`` and
    the legacy ``override``."""
    return resolved_permission_mode(autonomy, override)


def claude_code_config_conflicts(
    worker_env: AllowlistedEnv, *, autonomy: Autonomy, override: str | None, spawn_root: str
) -> tuple[str, ...]:
    """Each ambient setting that defeats the runner's wiring, rendered as its file and key."""
    found = detect_ambient_conflicts(
        claude_code_ambient_sources(worker_env, spawn_root=spawn_root),
        claude_code_permission_mode(autonomy, override),
    )
    return tuple(str(conflict) for conflict in found)
