"""``blizzard runner harness status`` prints exactly these lines, in this order — each enabled
harness contributes its own diagnostics, unbundled files, and bundle delivery lines in catalog
order, and the shared autonomy/bundle lines frame them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import CONFIG_FILENAME
from blizzard.runner.harness.internal.bundle_publisher import published_snapshot
from tests.runner_init_fakes import init_runner

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _no_ambient_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "blizzard.runner.harness.claude_code.ambient_conflicts.MANAGED_SETTINGS_PATHS",
        (tmp_path / "absent-managed.json",),
    )


def _runtime(tmp_path: Path, *, edit: tuple[str, str] | None = None) -> Path:
    runtime = tmp_path / "runner"
    assert init_runner().invoke(runner_group, ["init", str(runtime)]).exit_code == 0
    if edit is not None:
        path = runtime / CONFIG_FILENAME
        path.write_text(path.read_text().replace(edit[0], edit[1], 1))
    return runtime


def _status(runtime: Path) -> list[str]:
    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])
    assert result.exit_code == 0, result.output
    return result.output.splitlines()


def test_status_without_a_bundle_prints_each_harness_file_in_catalog_order(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    assert _status(runtime) == [
        "autonomy: dangerous (from [harness] autonomy)",
        "config_dir: none",
        f"worker settings file in effect: {runtime}/worker-settings.json",
        f"opencode worker config in effect: {runtime}/opencode-worker-config.json",
    ]


def test_status_names_the_legacy_permission_mode_as_the_autonomy_source(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, edit=("[harness]\n", 'harness_permission_mode = "auto"\n[harness]\n'))
    path = runtime / CONFIG_FILENAME
    path.write_text(path.read_text().replace('autonomy = "dangerous"\n', "", 1))

    assert _status(runtime)[0] == "autonomy: dangerous (from legacy harness_permission_mode)"


def test_status_reports_a_claude_code_conflict_after_the_autonomy_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    managed = tmp_path / "managed-settings.json"
    managed.write_text(json.dumps({"disableAllHooks": True}))
    monkeypatch.setattr("blizzard.runner.harness.claude_code.ambient_conflicts.MANAGED_SETTINGS_PATHS", (managed,))
    runtime = _runtime(tmp_path)

    assert _status(runtime)[:2] == [
        "autonomy: dangerous (from [harness] autonomy)",
        f"claude-code config conflict: managed settings {managed}: disableAllHooks",
    ]


def test_a_disabled_claude_code_reports_no_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    managed = tmp_path / "managed-settings.json"
    managed.write_text(json.dumps({"disableAllHooks": True}))
    monkeypatch.setattr("blizzard.runner.harness.claude_code.ambient_conflicts.MANAGED_SETTINGS_PATHS", (managed,))
    runtime = _runtime(tmp_path, edit=("[claude_code]\nenabled = true\n", "[claude_code]\nenabled = false\n"))

    assert not any("config conflict" in line for line in _status(runtime))


def test_status_with_a_published_bundle_prints_sources_snapshot_and_delivery(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "settings.json").write_text("{}")
    (bundle / "claude-code" / "mcp.json").write_text("{}")
    (bundle / "opencode").mkdir()
    (bundle / "opencode" / "opencode.json").write_text("{}")
    runtime = _runtime(tmp_path, edit=("[harness]\n", f'[harness]\nconfig_dir = "{bundle}"\n'))
    assert CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)]) is not None
    snapshot = published_snapshot(runtime)
    assert snapshot is not None

    assert _status(runtime) == [
        "autonomy: dangerous (from [harness] autonomy)",
        f"config_dir: {bundle}",
        f"claude-code: source {bundle}/claude-code; entry points: mcp.json, settings.json",
        f"opencode: source {bundle}/opencode; entry points: opencode.json",
        f"snapshot: {snapshot}",
        f"claude-code effective settings: {snapshot}/claude-code/settings.json",
        f"claude-code flags: --settings {snapshot}/claude-code/settings.json "
        f"--mcp-config={snapshot}/claude-code/mcp.json",
    ]
