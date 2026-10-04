"""OpenCode bundle identities, companion references and production binding wiring."""

from __future__ import annotations

import json
from concurrent.futures import Executor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from blizzard.runner.cli.runtime import _publish_harness_bundle
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessBundleError, HarnessComposition, published_snapshot
from blizzard.runner.harness.bundle_layouts import publish_harness_bundle
from blizzard.runner.harness.catalog import shared_inputs
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_bundle import (
    _compose,
    _directory_plugins,
    _merge_plugins,
    _plugins,
    ambient_plugin_sources,
    content_with_snapshot_references,
    plugin_identity,
)
from blizzard.runner.harness.internal.opencode_declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.internal.opencode_section import OpenCodeSection
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.sections import HarnessSections, section_of
from blizzard.runner.runtime import init_environment
from tests.harness_sections import opencode, sections, with_claude_code
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
        section = section_of(sections, OPENCODE_DECLARATION.harness_id)
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


@pytest.mark.parametrize(
    ("reference", "identity"),
    [
        ("@Scope/Tool@1.2.3", "@scope/tool"),
        ("@Scope/Tool", "@scope/tool"),
        ("Tool@2", "tool"),
        ("Tool", "tool"),
        ("file:///opt/My%20Plugin.TS", "my plugin"),
        ("file:///opt/Extra.mjs", "extra"),
    ],
)
def test_plugin_identity_names_the_registered_plugin(reference: str, identity: str) -> None:
    assert plugin_identity(reference) == identity


def test_directory_plugins_accept_only_plugin_suffixes(tmp_path: Path) -> None:
    for name in ("a.js", "b.ts", "C.mjs", "d.mts", "e.json", "f.md", "g"):
        (tmp_path / name).write_text("")
    assert _directory_plugins(tmp_path) == {
        "a": tmp_path / "a.js",
        "b": tmp_path / "b.ts",
        "c": tmp_path / "C.mjs",
        "d": tmp_path / "d.mts",
    }
    assert _directory_plugins(tmp_path / "missing") == {}


def test_directory_plugins_reject_a_duplicate_stem_naming_the_second_path(tmp_path: Path) -> None:
    (tmp_path / "a.js").write_text("")
    (tmp_path / "a.ts").write_text("")
    with pytest.raises(HarnessBundleError) as raised:
        _directory_plugins(tmp_path)
    assert raised.value.path == tmp_path / "a.ts"
    assert f"duplicate plugin 'a' at {tmp_path / 'a.js'} and {tmp_path / 'a.ts'}" in str(raised.value)


def test_merge_plugins_blames_the_incoming_path(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    with pytest.raises(HarnessBundleError) as raised:
        _merge_plugins({"x": first}, {"x": second})
    assert raised.value.path == second
    assert f"duplicate plugin 'x' at {first} and {second}" in str(raised.value)


def test_plugins_errors_carry_the_source_path(tmp_path: Path) -> None:
    source = tmp_path / "opencode.json"
    for bad in ("x", [""], [1]):
        with pytest.raises(HarnessBundleError, match="plugin must be an array of non-empty strings") as raised:
            _plugins({"plugin": bad}, source)
        assert raised.value.path == source
    with pytest.raises(HarnessBundleError) as raised:
        _plugins({"plugin": ["A", "a@1"]}, source)
    assert raised.value.path == source
    assert f"duplicate plugin 'a' at {source} and {source}" in str(raised.value)


def test_ambient_sources_ignore_a_plugins_directory_beside_an_ancestor_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    ancestor = tmp_path / "repo"
    cwd = ancestor / "sub"
    cwd.mkdir(parents=True)
    (ancestor / "opencode.json").write_text(json.dumps({"plugin": ["declared@1"]}))
    for sibling in ("plugins", "plugin"):
        (ancestor / sibling).mkdir()
        (ancestor / sibling / "unrelated.ts").write_text("")
    (ancestor / ".opencode" / "plugins").mkdir(parents=True)
    native = ancestor / ".opencode" / "plugins" / "native.ts"
    native.write_text("")
    (ancestor / ".opencode" / "plugin").mkdir()
    singular = ancestor / ".opencode" / "plugin" / "single.js"
    singular.write_text("")
    user = home / ".config" / "opencode" / "plugins"
    user.mkdir(parents=True)
    (user / "mine.ts").write_text("")
    assert ambient_plugin_sources(cwd, {"HOME": str(home)}) == {
        "mine": user / "mine.ts",
        "declared": ancestor / "opencode.json",
        "native": native,
        "single": singular,
    }


def test_ambient_sources_honor_xdg_config_home(tmp_path: Path) -> None:
    xdg = tmp_path / "xdg"
    (xdg / "opencode").mkdir(parents=True)
    config = xdg / "opencode" / "opencode.json"
    config.write_text(json.dumps({"plugin": ["p@1"]}))
    assert ambient_plugin_sources(tmp_path / "cwd", {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(xdg)}) == {
        "p": config
    }


def test_ambient_sources_report_unreadable_and_non_object_configs(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".config" / "opencode" / "opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text("{nope")
    with pytest.raises(HarnessBundleError, match="cannot inspect plugin declarations") as raised:
        ambient_plugin_sources(tmp_path, {"HOME": str(home)})
    assert raised.value.path == config
    config.write_text("[]")
    with pytest.raises(HarnessBundleError, match="must hold a JSON object") as raised:
        ambient_plugin_sources(tmp_path, {"HOME": str(home)})
    assert raised.value.path == config


def test_content_resolves_references_nested_in_lists(tmp_path: Path) -> None:
    document = {"agents": [{"prompt": "{file:a.txt}"}, ["{file:b.txt}", "plain", 3]]}
    assert json.loads(content_with_snapshot_references(json.dumps(document), tmp_path)) == {
        "agents": [
            {"prompt": f"{{file:{(tmp_path / 'a.txt').resolve()}}}"},
            [f"{{file:{(tmp_path / 'b.txt').resolve()}}}", "plain", 3],
        ]
    }


def _composition(tmp_path: Path, native: object | None) -> HarnessComposition:
    source, staged = tmp_path / "source", tmp_path / "staged"
    source.mkdir()
    staged.mkdir()
    if native is not None:
        (source / "opencode.json").write_text(json.dumps(native))
    return HarnessComposition(source_dir=source, staged_dir=staged)


def _worker(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "worker.json"
    path.write_text(json.dumps(document))
    return path


_WORKER = {"permission": {"question": "deny"}, "plugin": ["runner@1"]}


def test_compose_reports_a_missing_worker_config_at_its_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent.json"
    with pytest.raises(HarnessBundleError, match="runner OpenCode worker config is missing") as raised:
        _compose(_composition(tmp_path, None), missing)
    assert raised.value.path == missing


def test_compose_rejects_a_malformed_worker_config_at_its_path(tmp_path: Path) -> None:
    worker = _worker(tmp_path, {"permission": "nope", "plugin": []})
    with pytest.raises(HarnessBundleError) as raised:
        _compose(_composition(tmp_path, None), worker)
    assert raised.value.path == worker


def test_compose_rejects_a_non_object_permission_and_a_collision_at_the_native_path(tmp_path: Path) -> None:
    worker = _worker(tmp_path, _WORKER)
    composition = _composition(tmp_path, {"permission": []})
    with pytest.raises(HarnessBundleError, match="permission must be an object") as raised:
        _compose(composition, worker)
    assert raised.value.path == composition.source_dir / "opencode.json"
    (composition.source_dir / "opencode.json").write_text(json.dumps({"permission": {"question": "allow"}}))
    with pytest.raises(HarnessBundleError, match=r"permission\.question collides with runner-owned rule") as raised:
        _compose(composition, worker)
    assert raised.value.path == composition.source_dir / "opencode.json"


def test_compose_blames_the_operator_plugin_when_a_runner_plugin_duplicates_it(tmp_path: Path) -> None:
    worker = _worker(tmp_path, _WORKER)
    composition = _composition(tmp_path, {"plugin": ["runner@2"]})
    native = composition.source_dir / "opencode.json"
    with pytest.raises(HarnessBundleError) as raised:
        _compose(composition, worker)
    assert raised.value.path == native
    assert f"duplicate plugin 'runner' at {native} and {worker}" in str(raised.value)


def test_compose_rejects_an_operator_plugin_directory_duplicating_a_declared_plugin(tmp_path: Path) -> None:
    composition = _composition(tmp_path, {"plugin": ["tool@1"]})
    (composition.source_dir / "plugins").mkdir()
    (composition.source_dir / "plugins" / "tool.ts").write_text("")
    with pytest.raises(HarnessBundleError) as raised:
        _compose(composition, _worker(tmp_path, _WORKER))
    assert raised.value.path == composition.source_dir / "plugins" / "tool.ts"


def test_compose_writes_the_merged_document(tmp_path: Path) -> None:
    composition = _composition(tmp_path, {"permission": {"bash": "deny"}, "plugin": ["extra@1"]})
    _compose(composition, _worker(tmp_path, _WORKER))
    assert json.loads((composition.staged_dir / "opencode.json").read_text()) == {
        "permission": {"bash": "deny", "question": "deny"},
        "plugin": ["extra@1", "runner@1"],
    }


def test_mode_only_change_publishes_a_new_snapshot_that_keeps_the_mode(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    script = bundle / "claude-code" / "plugins" / "p" / "run.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    script.chmod(0o644)
    runtime = tmp_path / "runner"
    runtime.mkdir()
    first = publish_harness_bundle(bundle, runtime)
    assert (first.path / "claude-code" / "plugins" / "p" / "run.sh").stat().st_mode & 0o777 == 0o644
    assert publish_harness_bundle(bundle, runtime).path == first.path
    script.chmod(0o755)
    second = publish_harness_bundle(bundle, runtime)
    assert second.path != first.path
    assert (second.path / "claude-code" / "plugins" / "p" / "run.sh").stat().st_mode & 0o777 == 0o755


def test_runner_publication_composes_under_the_configured_root_autonomy_and_permission_mode(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "settings.json").write_text(
        json.dumps({"permissions": {"disableBypassPermissionsMode": True}})
    )
    root = tmp_path / "root"
    root.mkdir()
    config = RunnerConfig(root=root, db_url="sqlite://", harness_config_dir=bundle, autonomy=Autonomy.Normal)

    snapshot = _publish_harness_bundle(config)

    assert snapshot is not None
    assert snapshot.path.parent == root / "harness-config" / "snapshots"
    assert snapshot.source_dir == bundle
    composed = json.loads((snapshot.path / "claude-code" / "settings.json").read_text())
    assert composed["permissions"]["disableBypassPermissionsMode"] is True
    with pytest.raises(click.ClickException, match="disableBypassPermissionsMode"):
        _publish_harness_bundle(with_claude_code(config, permission_mode="bypassPermissions"))
