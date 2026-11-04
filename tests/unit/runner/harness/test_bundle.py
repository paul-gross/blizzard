"""The operator harness-config bundle: config key, loader validation, atomic publish, startup
wiring on ``host``/``tick``, the ``harness status`` verb, and the no-bundle identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.config import ConfigError
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessBundleError
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.internal.bundle_publisher import published_snapshot
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.wiring import publish_harness_bundle
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME
from tests.harness_sections import sections
from tests.host_files import load_runner_config
from tests.support_harness_bundle import bundle as _bundle
from tests.support_harness_bundle import runtime as _runtime


def test_missing_runner_config_blocks_publish_without_repointing_snapshot(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    missing = runtime / "missing-worker-config.json"
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, runtime, sections=sections(OpenCodeSection(worker_config_path=str(missing))))
    assert raised.value.path == missing
    assert published_snapshot(runtime) == first.path.resolve()


def _claude_code_bundle(tmp_path: Path, operator: dict[str, object]) -> Path:
    settings = tmp_path / "bundle" / "claude-code" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps(operator))
    return settings


def test_publish_composes_the_runner_wiring_into_the_snapshot_and_names_the_source_on_collision(
    tmp_path: Path,
) -> None:
    settings = _claude_code_bundle(tmp_path, {"model": "opus"})
    bundle = settings.parent.parent

    composed = json.loads((publish_harness_bundle(bundle, tmp_path).path / "claude-code" / "settings.json").read_text())
    assert composed["model"] == "opus"
    assert "SessionEnd" in composed["hooks"]
    assert composed["disableAllHooks"] is False

    settings.write_text(json.dumps({"disableAllHooks": True}))
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, tmp_path)
    assert raised.value.path == settings


def test_publish_composes_under_the_given_autonomy_and_section_permission_mode(tmp_path: Path) -> None:
    settings = _claude_code_bundle(tmp_path, {"permissions": {"disableBypassPermissionsMode": True}})
    bundle = settings.parent.parent

    with pytest.raises(HarnessBundleError) as dangerous:
        publish_harness_bundle(bundle, tmp_path)
    assert dangerous.value.path == settings
    normal = publish_harness_bundle(bundle, tmp_path, autonomy=Autonomy.Normal)
    composed = json.loads((normal.path / "claude-code" / "settings.json").read_text())
    assert composed["permissions"]["disableBypassPermissionsMode"] is True
    with pytest.raises(HarnessBundleError) as overridden:
        publish_harness_bundle(
            bundle,
            tmp_path,
            autonomy=Autonomy.Normal,
            sections=sections(ClaudeCodeSection(permission_mode="bypassPermissions")),
        )
    assert overridden.value.path == settings


@pytest.mark.parametrize("value", ['""', "5"])
def test_an_empty_or_non_string_config_dir_is_not_a_path_string(tmp_path: Path, value: str) -> None:
    runtime = _runtime(tmp_path)
    path = runtime / HOST_CONFIG_FILENAME
    path.write_text(path.read_text().replace("[runner.harness]\n", f"[runner.harness]\nconfig_dir = {value}\n", 1))
    with pytest.raises(ConfigError, match="config_dir must be a non-empty path string"):
        load_runner_config(runtime)
