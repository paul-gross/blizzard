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
from blizzard.runner.harness.internal.bundle_publisher import bundle_source_identity, published_snapshot
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.wiring import first_bindings, publish_harness_bundle
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME
from tests.support.harness_bundle import bundle as _bundle
from tests.support.harness_bundle import runtime as _runtime
from tests.support.harness_sections import sections
from tests.support.host_files import load_runner_config


def test_source_identity_follows_file_and_directory_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    file = external / "settings.json"
    file.write_text('{"model":"one"}')
    directory = external / "agents"
    directory.mkdir()
    agent = directory / "agent.md"
    agent.write_text("one")
    (bundle / "settings.json").symlink_to(file)
    (bundle / "agents").symlink_to(directory, target_is_directory=True)

    first = bundle_source_identity(bundle)
    assert first == bundle_source_identity(bundle)
    file.write_text('{"model":"two"}')
    second = bundle_source_identity(bundle)
    assert second != first
    agent.write_text("two")
    third = bundle_source_identity(bundle)
    assert third != second
    agent.chmod(0o755)
    assert bundle_source_identity(bundle) != third


def test_source_identity_handles_dangling_and_cyclic_symlinks(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "dangling").symlink_to("missing")
    (bundle / "cycle").symlink_to(bundle, target_is_directory=True)
    first = bundle_source_identity(bundle)
    assert first == bundle_source_identity(bundle)
    (bundle / "missing").write_text("now present")
    assert bundle_source_identity(bundle) != first


def test_missing_runner_config_blocks_publish_without_repointing_snapshot(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime, selection=first_bindings())
    missing = runtime / "missing-worker-config.json"
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(
            bundle,
            runtime,
            sections=sections(OpenCodeSection(worker_config_path=str(missing))),
            selection=first_bindings(),
        )
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

    composed = json.loads(
        (
            publish_harness_bundle(bundle, tmp_path, selection=first_bindings()).path / "claude-code" / "settings.json"
        ).read_text()
    )
    assert composed["model"] == "opus"
    assert "SessionEnd" in composed["hooks"]
    assert composed["disableAllHooks"] is False

    settings.write_text(json.dumps({"disableAllHooks": True}))
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, tmp_path, selection=first_bindings())
    assert raised.value.path == settings


def test_publish_composes_under_the_given_autonomy_and_section_permission_mode(tmp_path: Path) -> None:
    settings = _claude_code_bundle(tmp_path, {"permissions": {"disableBypassPermissionsMode": True}})
    bundle = settings.parent.parent

    with pytest.raises(HarnessBundleError) as dangerous:
        publish_harness_bundle(bundle, tmp_path, selection=first_bindings())
    assert dangerous.value.path == settings
    normal = publish_harness_bundle(bundle, tmp_path, autonomy=Autonomy.Normal, selection=first_bindings())
    composed = json.loads((normal.path / "claude-code" / "settings.json").read_text())
    assert composed["permissions"]["disableBypassPermissionsMode"] is True
    with pytest.raises(HarnessBundleError) as overridden:
        publish_harness_bundle(
            bundle,
            tmp_path,
            autonomy=Autonomy.Normal,
            sections=sections(ClaudeCodeSection(permission_mode="bypassPermissions")),
            selection=first_bindings(),
        )
    assert overridden.value.path == settings


@pytest.mark.parametrize("value", ['""', "5"])
def test_an_empty_or_non_string_config_dir_is_not_a_path_string(tmp_path: Path, value: str) -> None:
    runtime = _runtime(tmp_path)
    path = runtime / HOST_CONFIG_FILENAME
    path.write_text(path.read_text().replace("[runner.harness]\n", f"[runner.harness]\nconfig_dir = {value}\n", 1))
    with pytest.raises(ConfigError, match="config_dir must be a non-empty path string"):
        load_runner_config(runtime)
