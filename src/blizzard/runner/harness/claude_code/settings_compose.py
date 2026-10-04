"""Composes the operator's Claude Code ``settings.json`` with the runner's required wiring.

A pure function from two dicts to one, stdlib only. Hook arrays keep operator groups first
with the runner's appended; ``permissions.deny`` is the operator's list plus the runner's
entries not already present; every other key passes through. A key the operator set that
the runner owns, or one that would silently defeat the runner's wiring, is a collision."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from blizzard.runner.harness.autonomy import Autonomy

#: Claude Code's own vocabulary for each runner-wide autonomy value (`--permission-mode`).
PERMISSION_MODES = {
    Autonomy.Normal: "manual",
    Autonomy.Auto: "auto",
    Autonomy.Dangerous: "bypassPermissions",
}

_BYPASS = "bypassPermissions"
_RULE_LISTS = ("allow", "ask", "deny")


class SettingsCollision(Exception):
    """The operator's settings conflict with runner wiring; ``json_path`` names the key."""

    def __init__(self, json_path: str, reason: str) -> None:
        super().__init__(f"{json_path}: {reason}")
        self.json_path = json_path
        self.reason = reason


def resolved_permission_mode(autonomy: Autonomy, override: str | None) -> str | None:
    """The ``--permission-mode`` value the adapter passes, or ``None`` when none is passed."""
    if override is not None:
        return override or None
    return PERMISSION_MODES[autonomy]


def compose_settings(
    operator: Mapping[str, Any], runner: Mapping[str, Any], *, permission_mode: str | None
) -> dict[str, Any]:
    """The operator's document with the runner's wiring added; raises :class:`SettingsCollision`."""
    composed: dict[str, Any] = copy.deepcopy(dict(operator))
    runner_hooks = _object(runner.get("hooks"), "hooks")
    runner_deny = _rules(runner.get("permissions", {}).get("deny"), "permissions.deny")
    _check_flags(composed, permission_mode)
    permissions = _object(composed.get("permissions", {}), "permissions")
    rules = {name: _rules(permissions.get(name, []), f"permissions.{name}") for name in _RULE_LISTS}
    for name in ("allow", "ask"):
        for index, rule in enumerate(rules[name]):
            if _tool_name(rule) in runner_deny:
                raise SettingsCollision(f"permissions.{name}[{index}]", f"{rule!r} names a tool the runner denies")
    composed["hooks"] = _merge_hooks(_object(composed.get("hooks", {}), "hooks"), runner_hooks)
    permissions["deny"] = [*rules["deny"], *(tool for tool in runner_deny if tool not in rules["deny"])]
    composed["permissions"] = permissions
    composed["disableAllHooks"] = bool(runner.get("disableAllHooks", False))
    return composed


def _check_flags(operator: Mapping[str, Any], permission_mode: str | None) -> None:
    if operator.get("disableAllHooks") is True:
        raise SettingsCollision("disableAllHooks", "would silence the runner's heartbeat and session-end hooks")
    permissions = operator.get("permissions")
    if not isinstance(permissions, dict):
        return
    if "defaultMode" in permissions:
        raise SettingsCollision("permissions.defaultMode", "the runner's [harness] autonomy owns the permission mode")
    if permissions.get("disableBypassPermissionsMode") and permission_mode == _BYPASS:
        raise SettingsCollision(
            "permissions.disableBypassPermissionsMode", f"conflicts with the resolved {_BYPASS} permission mode"
        )


def _merge_hooks(operator: dict[str, Any], runner: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(operator)
    for event, runner_groups in runner.items():
        groups = _list(merged.get(event, []), f"hooks.{event}")
        merged[event] = [*groups, *runner_groups]
    return merged


def _tool_name(rule: str) -> str:
    return rule.split("(", 1)[0].strip()


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsCollision(path, "must be a JSON object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise SettingsCollision(path, "must be a JSON array")
    return value


def _rules(value: Any, path: str) -> list[str]:
    rules = _list(value, path)
    for index, rule in enumerate(rules):
        if not isinstance(rule, str):
            raise SettingsCollision(f"{path}[{index}]", "must be a string")
    return rules
