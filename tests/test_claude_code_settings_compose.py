"""The Claude Code settings composer: operator-first merge order and every collision it refuses."""

from __future__ import annotations

from typing import Any

import pytest

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.internal.claude_code_denials import CLAUDE_CODE_DENIED_TOOLS
from blizzard.runner.harness.internal.claude_code_settings_compose import (
    SettingsCollision,
    compose_settings,
    resolved_permission_mode,
)
from blizzard.runner.harness.worker_settings import WorkerSettings

pytestmark = pytest.mark.unit

_RUNNER = WorkerSettings.of().document
_OP_HOOK = {"hooks": [{"type": "command", "command": "operator-hook"}]}


def _compose(operator: dict[str, Any], mode: str | None = "bypassPermissions") -> dict[str, Any]:
    return compose_settings(operator, _RUNNER, permission_mode=mode)


def test_an_empty_operator_document_yields_the_runner_wiring() -> None:
    assert _compose({}) == _RUNNER


def test_hook_groups_keep_operator_first_and_runner_appended() -> None:
    composed = _compose({"hooks": {"PostToolUse": [_OP_HOOK], "Stop": [_OP_HOOK]}})

    assert composed["hooks"]["PostToolUse"] == [_OP_HOOK, *_RUNNER["hooks"]["PostToolUse"]]
    assert composed["hooks"]["Stop"] == [_OP_HOOK]
    assert composed["hooks"]["SessionEnd"] == _RUNNER["hooks"]["SessionEnd"]


def test_deny_is_the_operators_list_plus_the_runners_missing_entries() -> None:
    composed = _compose({"permissions": {"deny": ["WebFetch", "Monitor"]}})

    assert composed["permissions"]["deny"] == [
        "WebFetch",
        "Monitor",
        *(t for t in CLAUDE_CODE_DENIED_TOOLS if t != "Monitor"),
    ]


def test_unrelated_keys_pass_through_untouched() -> None:
    operator = {
        "model": "opus",
        "env": {"A": "1"},
        "permissions": {"allow": ["Bash(ls:*)"], "additionalDirectories": ["/x"]},
    }

    composed = _compose(operator)

    assert composed["model"] == "opus"
    assert composed["env"] == {"A": "1"}
    assert composed["permissions"]["allow"] == ["Bash(ls:*)"]
    assert composed["permissions"]["additionalDirectories"] == ["/x"]


def test_the_operator_document_is_not_mutated() -> None:
    operator = {"hooks": {"PostToolUse": [_OP_HOOK]}, "permissions": {"deny": ["WebFetch"]}}
    snapshot = {"hooks": {"PostToolUse": [_OP_HOOK]}, "permissions": {"deny": ["WebFetch"]}}

    _compose(operator)

    assert operator == snapshot


def test_the_runner_pins_disable_all_hooks_false() -> None:
    assert _compose({})["disableAllHooks"] is False


@pytest.mark.parametrize(
    ("operator", "path"),
    [
        ({"disableAllHooks": True}, "disableAllHooks"),
        ({"permissions": {"allow": ["Read", "Monitor"]}}, "permissions.allow[1]"),
        ({"permissions": {"ask": ["CronCreate(*)"]}}, "permissions.ask[0]"),
        ({"permissions": {"defaultMode": "plan"}}, "permissions.defaultMode"),
        ({"permissions": {"disableBypassPermissionsMode": "disable"}}, "permissions.disableBypassPermissionsMode"),
        ({"hooks": []}, "hooks"),
        ({"hooks": {"PostToolUse": {}}}, "hooks.PostToolUse"),
        ({"permissions": []}, "permissions"),
        ({"permissions": {"deny": "Monitor"}}, "permissions.deny"),
        ({"permissions": {"allow": ["Read", 3]}}, "permissions.allow[1]"),
    ],
)
def test_each_collision_names_its_json_path(operator: dict[str, Any], path: str) -> None:
    with pytest.raises(SettingsCollision) as caught:
        _compose(operator)

    assert caught.value.json_path == path


@pytest.mark.parametrize(
    ("operator", "path", "reason"),
    [
        ({"disableAllHooks": True}, "disableAllHooks", "would silence the runner's heartbeat and session-end hooks"),
        (
            {"permissions": {"defaultMode": "plan"}},
            "permissions.defaultMode",
            "the runner's [harness] autonomy owns the permission mode",
        ),
        (
            {"permissions": {"disableBypassPermissionsMode": True}},
            "permissions.disableBypassPermissionsMode",
            "conflicts with the resolved bypassPermissions permission mode",
        ),
    ],
)
def test_each_flag_collision_states_its_reason(operator: dict[str, Any], path: str, reason: str) -> None:
    with pytest.raises(SettingsCollision) as caught:
        _compose(operator, "bypassPermissions")

    assert (caught.value.json_path, caught.value.reason) == (path, reason)


def test_disable_bypass_only_collides_under_bypass_permissions() -> None:
    operator = {"permissions": {"disableBypassPermissionsMode": "disable"}}

    assert _compose(operator, "auto")["permissions"]["disableBypassPermissionsMode"] == "disable"


def test_disable_all_hooks_false_is_allowed() -> None:
    assert _compose({"disableAllHooks": False})["disableAllHooks"] is False


@pytest.mark.parametrize(
    ("autonomy", "override", "expected"),
    [
        (Autonomy.Normal, None, "manual"),
        (Autonomy.Dangerous, None, "bypassPermissions"),
        (Autonomy.Auto, "plan", "plan"),
        (Autonomy.Auto, "", None),
    ],
)
def test_resolved_permission_mode_mirrors_the_adapters_argv(
    autonomy: Autonomy, override: str | None, expected: str | None
) -> None:
    assert resolved_permission_mode(autonomy, override) == expected
