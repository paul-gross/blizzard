"""OpenCode bundle identities, companion references and production binding wiring."""

from __future__ import annotations

import json
from concurrent.futures import Executor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from blizzard.runner.cli.runtime import _publish_harness_bundle
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.bundle import HarnessBundleError, published_snapshot
from blizzard.runner.harness.bundle_layouts import publish_harness_bundle
from blizzard.runner.harness.catalog import shared_inputs
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_bundle import (
    _merge_plugins,
    _plugins,
    ambient_plugin_sources,
    content_with_snapshot_references,
)
from blizzard.runner.harness.internal.opencode_declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.internal.opencode_section import OpenCodeSection
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.sections import HarnessSections
from blizzard.runner.runtime import init_environment
from tests.harness_sections import opencode, sections
from tests.runner_fakes import FakeProbe

pytestmark = pytest.mark.unit


def test_explicit_plugins_reject_duplicate_identities_with_both_sources(tmp_path: Path) -> None:
    operator = tmp_path / "operator" / "opencode.json"
    runner = tmp_path / "runner" / "opencode.json"
    found = _plugins({"plugin": ["@example/tool@1"]}, operator)
    assert found == {"@example/tool": operator}
    with pytest.raises(HarnessBundleError) as raised:
        _merge_plugins(found, _plugins({"plugin": ["@example/tool@2"]}, runner))
    assert str(operator) in str(raised.value)
    assert str(runner) in str(raised.value)
    with pytest.raises(HarnessBundleError, match="duplicate plugin"):
        _plugins({"plugin": ["file:///tmp/Extra.ts", "file:///else/extra.js"]}, operator)


def test_ambient_jsonc_and_native_plugin_directory_are_discovered(tmp_path: Path) -> None:
    home = tmp_path / "home"
    user = home / ".config" / "opencode"
    user.mkdir(parents=True)
    config = user / "opencode.jsonc"
    config.write_text('// note\n{"plugin": ["@example/extra@1",],}')
    cwd = tmp_path / "project"
    directory = cwd / ".opencode" / "plugins"
    directory.mkdir(parents=True)
    plugin = directory / "Tool.ts"
    plugin.write_text("export default () => ({})")
    sources = ambient_plugin_sources(cwd, {"HOME": str(home)})
    assert sources["@example/extra"] == config
    assert sources["tool"] == plugin
    config.write_text('{"plugin": ["file:///elsewhere/tool.js"]}')
    with pytest.raises(HarnessBundleError) as raised:
        ambient_plugin_sources(cwd, {"HOME": str(home)})
    assert str(config) in str(raised.value)
    assert str(plugin) in str(raised.value)


def test_content_rewrites_only_relative_companions(tmp_path: Path) -> None:
    document = {"relative": "{file:./prompts/a.txt}", "absolute": "{file:/etc/other.txt}", "home": "{file:~/other.txt}"}
    content = content_with_snapshot_references(json.dumps(document), tmp_path)
    assert json.loads(content) == {
        "relative": f"{{file:{tmp_path / 'prompts' / 'a.txt'}}}",
        "absolute": "{file:/etc/other.txt}",
        "home": "{file:~/other.txt}",
    }


def test_registry_binds_published_config_and_generated_config_without_bundle(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    config = init_environment(tmp_path / "runtime")
    assert opencode(config).worker_config_path is not None
    worker = opencode(config).worker_config_at(config.root)
    assert worker.is_file()
    bundle = tmp_path / "bundle"
    native = bundle / "opencode" / "opencode.json"
    native.parent.mkdir(parents=True)
    native.write_text(json.dumps({"permission": {"bash": "deny"}, "plugin": ["extra@1"]}))
    snapshot = publish_harness_bundle(bundle, config.root, sections=config.harness_sections)
    probe = FakeProbe()
    launcher = ProcessLauncher(probe, executor=spawn_executor)
    bundled = replace(config, harness_config_dir=bundle)
    binding = OPENCODE_DECLARATION.binding(opencode(bundled), shared_inputs(bundled), process=probe, launcher=launcher)
    assert isinstance(binding.adapter, OpenCodeAdapter)
    env = binding.adapter._config_env()
    assert env["OPENCODE_CONFIG"] == str(snapshot.path / "opencode" / "opencode.json")
    assert env["OPENCODE_CONFIG_DIR"] == str(snapshot.path / "opencode")
    composed = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert composed["permission"] == {"bash": "deny", "question": "deny"}
    assert composed["plugin"] == ["extra@1", *json.loads(worker.read_text())["plugin"]]
    assert published_snapshot(config.root) == snapshot.path.resolve()
    plain = OPENCODE_DECLARATION.binding(opencode(config), shared_inputs(config), process=probe, launcher=launcher)
    assert isinstance(plain.adapter, OpenCodeAdapter)
    plain_env = plain.adapter._config_env()
    assert plain_env["OPENCODE_CONFIG"] == str(worker)
    assert "OPENCODE_CONFIG_DIR" not in plain_env


def test_runner_publication_passes_the_configured_worker_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "bundle"
    worker = tmp_path / "custom" / "worker.json"
    observed: list[tuple[Path, Path, str | None]] = []

    def publish(source: Path, root: Path, *, sections: HarnessSections, **_: object) -> SimpleNamespace:
        section = sections.of(OPENCODE_DECLARATION.harness_id)
        assert isinstance(section, OpenCodeSection)
        observed.append((source, root, section.worker_config_path))
        return SimpleNamespace(summary=lambda: "published")

    monkeypatch.setattr("blizzard.runner.cli.runtime.publish_harness_bundle", publish)
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        harness_config_dir=bundle,
        harness_sections=sections(OpenCodeSection(worker_config_path=str(worker))),
    )
    _publish_harness_bundle(config)
    assert observed == [(bundle, tmp_path, str(worker))]
