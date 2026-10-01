"""Ambient Claude Code settings the runner cannot override: each managed rule, the scoping that
keeps user/project files from raising a conflict the runner's own pin already neutralizes, and the
probe and ``harness status`` reporting the file and key without any content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.harness.health import HarnessHealthCause, HarnessHealthEvidence, evaluate_harness_health
from blizzard.runner.harness.internal.claude_code_ambient import (
    BYPASS_PERMISSIONS,
    AmbientSources,
    SettingsScope,
    detect_ambient_conflicts,
    user_config_dir,
)
from blizzard.runner.harness.internal.claude_code_health import ClaudeCodeHealthProbe

pytestmark = pytest.mark.component

_SECRET = "sentinel-secret-value"


def _write(path: Path, document: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def _sources(tmp_path: Path, managed: dict[str, object] | None = None) -> AmbientSources:
    managed_path = tmp_path / "etc" / "managed-settings.json"
    if managed is not None:
        _write(managed_path, managed)
    return AmbientSources((managed_path,), tmp_path / "home" / ".claude", tmp_path / "project")


@pytest.mark.parametrize(
    "key",
    ["disableAllHooks", "allowManagedHooksOnly", "allowManagedPermissionRulesOnly"],
)
def test_each_managed_rule_names_the_file_and_key(tmp_path: Path, key: str) -> None:
    sources = _sources(tmp_path, {key: True, "note": _SECRET})

    (conflict,) = detect_ambient_conflicts(sources, "auto")

    assert conflict.scope is SettingsScope.MANAGED
    assert conflict.path == sources.managed_paths[0]
    assert conflict.key == key
    assert _SECRET not in str(conflict)


def test_managed_disable_bypass_only_conflicts_under_bypass_permissions(tmp_path: Path) -> None:
    sources = _sources(tmp_path, {"permissions": {"disableBypassPermissionsMode": "disable"}})

    assert detect_ambient_conflicts(sources, "auto") == ()
    (conflict,) = detect_ambient_conflicts(sources, BYPASS_PERMISSIONS)
    assert conflict.key == "permissions.disableBypassPermissionsMode"


def test_a_false_or_absent_managed_key_is_no_conflict(tmp_path: Path) -> None:
    assert detect_ambient_conflicts(_sources(tmp_path, {"disableAllHooks": False}), "auto") == ()
    assert detect_ambient_conflicts(_sources(tmp_path), "auto") == ()


def test_unreadable_managed_settings_hold_no_conflict(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    sources.managed_paths[0].parent.mkdir(parents=True)
    sources.managed_paths[0].write_text("{")

    assert detect_ambient_conflicts(sources, "auto") == ()


def test_user_and_project_disable_all_hooks_is_neutralized_not_reported(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    assert sources.user_dir is not None
    assert sources.project_dir is not None
    _write(sources.user_dir / "settings.json", {"disableAllHooks": True})
    _write(sources.project_dir / ".claude" / "settings.json", {"disableAllHooks": True})
    _write(sources.project_dir / ".claude" / "settings.local.json", {"disableAllHooks": True})

    assert detect_ambient_conflicts(sources, BYPASS_PERMISSIONS) == ()


def test_user_config_dir_follows_the_workers_env() -> None:
    assert user_config_dir({"CLAUDE_CONFIG_DIR": "/c", "HOME": "/h"}) == Path("/c")
    assert user_config_dir({"HOME": "/h"}) == Path("/h/.claude")
    assert user_config_dir({}) is None


def test_the_probe_reports_a_conflict_that_the_evaluator_turns_into_config_conflict(tmp_path: Path) -> None:
    sources = _sources(tmp_path, {"disableAllHooks": True})
    probe = ClaudeCodeHealthProbe("claude", ambient_sources=sources, permission_mode="auto")

    conflicts = probe.config_conflicts()
    result = evaluate_harness_health(
        HarnessHealthEvidence(
            harness_id="claude_code",
            binary_present=True,
            version_declared=False,
            version_admitted=None,
            version_classification=None,
            authenticated=True,
            unmapped_tiers=(),
            selftest_failed=None,
            corpus_backed=False,
            config_conflicts=conflicts,
        )
    )

    assert conflicts == (f"managed settings {sources.managed_paths[0]}: disableAllHooks",)
    assert result.available is False
    assert result.cause is HarnessHealthCause.CONFIG_CONFLICT


def test_a_probe_without_injected_sources_reports_nothing() -> None:
    assert ClaudeCodeHealthProbe("claude").config_conflicts() == ()


def test_status_names_the_conflicting_file_and_key_without_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runner"
    assert CliRunner().invoke(runner_group, ["init", str(runtime)]).exit_code == 0
    managed = _write(tmp_path / "managed-settings.json", {"allowManagedHooksOnly": True, "note": _SECRET})
    monkeypatch.setattr("blizzard.runner.harness.ambient_conflicts.MANAGED_SETTINGS_PATHS", (managed,))

    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])

    assert result.exit_code == 0, result.output
    assert f"claude-code config conflict: managed settings {managed}: allowManagedHooksOnly" in result.output
    assert _SECRET not in result.output
