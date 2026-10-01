"""Ambient Claude Code settings the runner's composed ``--settings`` cannot override.

A rule table over the settings files a worker would load around the bundle; a hit is reported by file
and key, never content. Managed settings outrank every source, so those rules are checked in full; user,
project, and local carry only keys proven to survive the composed file (an ambient ``disableAllHooks``
there is neutralized by the runner's own pin)."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

BYPASS_PERMISSIONS = "bypassPermissions"
MANAGED_SETTINGS_PATHS = (Path("/etc/claude-code/managed-settings.json"),)


class SettingsScope(StrEnum):
    MANAGED = "managed"
    USER = "user"
    PROJECT = "project"
    LOCAL = "local"


@dataclass(frozen=True)
class AmbientRule:
    """One forbidden key in one scope; ``applies`` narrows it by the resolved permission mode."""

    scope: SettingsScope
    key: str
    applies: Callable[[str | None], bool] = lambda _mode: True

    def hit(self, document: Mapping[str, Any], permission_mode: str | None) -> bool:
        if not self.applies(permission_mode):
            return False
        value: Any = document
        for part in self.key.split("."):
            if not isinstance(value, Mapping) or part not in value:
                return False
            value = value[part]
        return bool(value)


@dataclass(frozen=True)
class AmbientConflict:
    """A forbidden key found in an ambient settings file."""

    scope: SettingsScope
    path: Path
    key: str

    def __str__(self) -> str:
        return f"{self.scope.value} settings {self.path}: {self.key}"


@dataclass(frozen=True)
class AmbientSources:
    """Where the settings files live, injected: nothing here reads the daemon's own environment."""

    managed_paths: tuple[Path, ...]
    user_dir: Path | None
    project_dir: Path | None


RULES = (
    AmbientRule(SettingsScope.MANAGED, "disableAllHooks"),
    AmbientRule(SettingsScope.MANAGED, "allowManagedHooksOnly"),
    AmbientRule(SettingsScope.MANAGED, "allowManagedPermissionRulesOnly"),
    AmbientRule(
        SettingsScope.MANAGED, "permissions.disableBypassPermissionsMode", lambda mode: mode == BYPASS_PERMISSIONS
    ),
)


def user_config_dir(env: Mapping[str, str]) -> Path | None:
    """Claude Code's user config dir as the process owning ``env`` would resolve it:
    ``CLAUDE_CONFIG_DIR`` when non-empty, otherwise ``HOME/.claude``."""
    configured = env.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    home = env.get("HOME")
    return Path(home) / ".claude" if home else None


def detect_ambient_conflicts(
    sources: AmbientSources, permission_mode: str | None, rules: tuple[AmbientRule, ...] = RULES
) -> tuple[AmbientConflict, ...]:
    """Every rule hit across the injected settings files; an unreadable or non-object file holds none."""
    conflicts: list[AmbientConflict] = []
    for scope, path in _files(sources):
        document = _read(path)
        if document is None:
            continue
        conflicts.extend(
            AmbientConflict(scope, path, rule.key)
            for rule in rules
            if rule.scope is scope and rule.hit(document, permission_mode)
        )
    return tuple(conflicts)


def _files(sources: AmbientSources) -> list[tuple[SettingsScope, Path]]:
    files = [(SettingsScope.MANAGED, path) for path in sources.managed_paths]
    if sources.user_dir is not None:
        files.append((SettingsScope.USER, sources.user_dir / "settings.json"))
    if sources.project_dir is not None:
        files.append((SettingsScope.PROJECT, sources.project_dir / ".claude" / "settings.json"))
        files.append((SettingsScope.LOCAL, sources.project_dir / ".claude" / "settings.local.json"))
    return files


def _read(path: Path) -> Mapping[str, Any] | None:
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None
