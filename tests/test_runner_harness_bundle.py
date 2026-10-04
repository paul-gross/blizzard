"""The operator harness-config bundle: config key, loader validation, atomic publish, startup
wiring on ``host``/``tick``, the ``harness status`` verb, and the no-bundle identity."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import CONFIG_FILENAME, ConfigError, RunnerConfig
from blizzard.runner.harness import bundle as bundle_module
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessBundleError, published_snapshot
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.opencode.bundle import check_ambient_plugins, plugin_identity
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.wiring import publish_harness_bundle
from tests.harness_sections import sections

pytestmark = pytest.mark.component

_SECRET = "sentinel-secret-value"


def test_composed_opencode_config_preserves_settings_and_denials(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    native = bundle / "opencode" / "opencode.json"
    shutil.rmtree(bundle / "opencode" / "prompts")
    native.write_text(json.dumps({"model": "example/model", "permission": {"bash": "deny"}, "plugin": ["extra@1"]}))
    snapshot = publish_harness_bundle(bundle, runtime)
    composed = json.loads((snapshot.path / "opencode" / "opencode.json").read_text())
    assert composed["model"] == "example/model"
    assert composed["permission"] == {"bash": "deny", "question": "deny"}
    worker = json.loads((runtime / "opencode-worker-config.json").read_text())
    assert composed["plugin"] == ["extra@1", *worker["plugin"]]


def test_collision_keeps_previous_snapshot(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    native = bundle / "opencode" / "opencode.json"
    shutil.rmtree(bundle / "opencode" / "prompts")
    native.write_text('{"permission": {"question": "allow"}}')
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, runtime)
    assert raised.value.path == native
    assert published_snapshot(runtime) == first.path.resolve()


def test_runner_plugin_collision_names_native_source_and_keeps_snapshot(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    worker = json.loads((runtime / "opencode-worker-config.json").read_text())
    native = bundle / "opencode" / "opencode.json"
    shutil.rmtree(bundle / "opencode" / "prompts")
    native.write_text(json.dumps({"plugin": worker["plugin"]}))

    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, runtime)
    assert raised.value.path == native
    assert str(runtime / "opencode-worker-config.json") in str(raised.value)
    assert published_snapshot(runtime) == first.path.resolve()


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


def test_plugin_identities_across_scopes(tmp_path: Path) -> None:
    assert plugin_identity("@vendor/extra@1.2") == "@vendor/extra"
    assert plugin_identity("file:///tmp/Extra.ts") == "extra"
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    (bundle / "opencode" / "plugins").mkdir()
    (bundle / "opencode" / "plugins" / "extra.ts").write_text("export default () => ({})")
    shutil.rmtree(bundle / "opencode" / "prompts")
    (bundle / "opencode" / "opencode.json").write_text('{"plugin": ["file:///tmp/extra.ts"]}')
    with pytest.raises(HarnessBundleError, match="duplicate plugin"):
        publish_harness_bundle(bundle, runtime)
    (bundle / "opencode" / "opencode.json").write_text("{}")
    snapshot = publish_harness_bundle(bundle, runtime)
    project = tmp_path / "project"
    (project / ".opencode").mkdir(parents=True)
    (project / ".opencode" / "opencode.json").write_text('{"plugin": ["file:///elsewhere/extra.js"]}')
    with pytest.raises(HarnessBundleError, match=r"extra\.ts.*opencode\.json"):
        check_ambient_plugins(snapshot.path / "opencode", project, {"HOME": str(tmp_path / "empty")})
    (project / ".opencode" / "opencode.json").write_text("{}")
    home = tmp_path / "home"
    user = home / ".config" / "opencode"
    user.mkdir(parents=True)
    (user / "opencode.jsonc").write_text('// user plugins\n{"plugin": ["file:///elsewhere/extra.js",],}')
    with pytest.raises(HarnessBundleError, match=r"extra\.ts.*opencode\.jsonc"):
        check_ambient_plugins(snapshot.path / "opencode", project, {"HOME": str(home)})


def test_ambient_directory_plugins_collide_with_operator_and_each_other(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    (bundle / "opencode" / "plugins").mkdir()
    (bundle / "opencode" / "plugins" / "duplicate.ts").write_text("export const Plugin = () => ({})")
    snapshot = publish_harness_bundle(bundle, runtime)
    project = tmp_path / "project"
    local_plugins = project / ".opencode" / "plugins"
    local_plugins.mkdir(parents=True)
    (local_plugins / "DUPLICATE.js").write_text("export const Plugin = () => ({})")
    home = tmp_path / "home"
    global_plugins = home / ".config" / "opencode" / "plugins"
    global_plugins.mkdir(parents=True)
    with pytest.raises(HarnessBundleError, match=r"duplicate\.ts.*DUPLICATE\.js"):
        check_ambient_plugins(snapshot.path / "opencode", project, {"HOME": str(home)})
    (bundle / "opencode" / "plugins" / "duplicate.ts").unlink()
    snapshot = publish_harness_bundle(bundle, runtime)
    (global_plugins / "duplicate.mjs").write_text("export const Plugin = () => ({})")
    with pytest.raises(HarnessBundleError, match=r"duplicate\.mjs.*DUPLICATE\.js"):
        check_ambient_plugins(snapshot.path / "opencode", project, {"HOME": str(home)})


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "claude-code").mkdir(parents=True)
    (root / "claude-code" / "settings.json").write_text(json.dumps({"note": _SECRET}))
    (root / "opencode" / "prompts").mkdir(parents=True)
    (root / "opencode" / "opencode.json").write_text(json.dumps({"agent": {"x": {"prompt": "{file:./prompts/p.txt}"}}}))
    (root / "opencode" / "prompts" / "p.txt").write_text(_SECRET)
    return root


def _runtime(tmp_path: Path, config_dir: Path | str | None = None) -> Path:
    runtime = tmp_path / "runner"
    assert CliRunner().invoke(runner_group, ["init", str(runtime)]).exit_code == 0
    if config_dir is not None:
        path = runtime / CONFIG_FILENAME
        path.write_text(path.read_text().replace("[harness]\n", f'[harness]\nconfig_dir = "{config_dir}"\n', 1))
    return runtime


def _fingerprint(root: Path) -> dict[str, tuple[str, int, int]]:
    return {
        str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mode, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------------- #
# Config key.


def test_config_dir_absent_is_none(tmp_path: Path) -> None:
    assert RunnerConfig.load(_runtime(tmp_path)).harness_config_dir is None


def test_config_dir_expands_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = RunnerConfig.load(_runtime(tmp_path, "~/harness"))
    assert config.harness_config_dir == tmp_path / "harness"


def test_relative_config_dir_names_key_and_toml(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, "rel/dir")
    with pytest.raises(ConfigError, match=r"\[harness\] config_dir.*rel/dir.*blizzard-runner\.toml"):
        RunnerConfig.load(runtime)


@pytest.mark.unit
@pytest.mark.parametrize("value", ['""', "5"])
def test_an_empty_or_non_string_config_dir_is_not_a_path_string(tmp_path: Path, value: str) -> None:
    runtime = _runtime(tmp_path)
    path = runtime / CONFIG_FILENAME
    path.write_text(path.read_text().replace("[harness]\n", f"[harness]\nconfig_dir = {value}\n", 1))
    with pytest.raises(ConfigError, match="config_dir must be a non-empty path string"):
        RunnerConfig.load(runtime)


def test_config_dir_inside_effective_root_is_rejected(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    inside = runtime.resolve() / "harness-config" / "snapshots"
    path = runtime / CONFIG_FILENAME
    path.write_text(path.read_text().replace("[harness]\n", f'[harness]\nconfig_dir = "{inside}"\n', 1))
    with pytest.raises(ConfigError, match="inside it"):
        RunnerConfig.load(runtime)


def test_scaffold_round_trip_has_no_bundle(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    assert "# config_dir =" in (runtime / CONFIG_FILENAME).read_text()
    config = RunnerConfig.load(runtime)
    assert config.harness_config_dir is None
    assert RunnerConfig.load(runtime).to_toml() == config.to_toml()


def test_to_toml_writes_a_set_config_dir(tmp_path: Path) -> None:
    config = RunnerConfig.load(_runtime(tmp_path, tmp_path / "b"))
    assert f'config_dir = "{tmp_path / "b"}"' in config.to_toml()


# --------------------------------------------------------------------------- #
# Loader validation: every failure names its path.


def test_missing_config_dir_fails_naming_it(tmp_path: Path) -> None:
    with pytest.raises(HarnessBundleError, match="nope"):
        publish_harness_bundle(tmp_path / "nope", tmp_path / "rt")


def test_stray_bundle_root_entry_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "extra").mkdir()
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, tmp_path / "rt")
    assert raised.value.path == bundle / "extra"


def test_stray_harness_entry_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "claude-code" / "notes.txt").write_text("x")
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, tmp_path / "rt")
    assert raised.value.path == bundle / "claude-code" / "notes.txt"


def test_malformed_json_names_file_line_and_column(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "opencode" / "opencode.json").write_text("{\n  nope")
    with pytest.raises(HarnessBundleError, match=r"opencode\.json.*line 2 column"):
        publish_harness_bundle(bundle, tmp_path / "rt")


def test_json_that_is_not_an_object_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "opencode" / "opencode.json").write_text("[]")
    with pytest.raises(HarnessBundleError, match="JSON object"):
        publish_harness_bundle(bundle, tmp_path / "rt")


def test_companion_escaping_the_harness_dir_fails(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "opencode" / "opencode.json").write_text(json.dumps({"p": "{file:../claude-code/settings.json}"}))
    with pytest.raises(HarnessBundleError, match="escapes"):
        publish_harness_bundle(bundle, tmp_path / "rt")


def test_missing_companion_fails_naming_it(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "opencode" / "prompts" / "p.txt").unlink()
    with pytest.raises(HarnessBundleError) as raised:
        publish_harness_bundle(bundle, tmp_path / "rt")
    assert raised.value.path == bundle / "opencode" / "prompts" / "p.txt"


def test_a_harness_directory_is_validated_even_alone(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "bad.txt").write_text("x")
    with pytest.raises(HarnessBundleError):
        publish_harness_bundle(bundle, tmp_path / "rt")


# --------------------------------------------------------------------------- #
# Companions and snapshot layout.


def test_snapshot_mirrors_layout_and_copies_relative_companions(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    snapshot = publish_harness_bundle(bundle, _runtime(tmp_path))

    assert (snapshot.path / "opencode" / "prompts" / "p.txt").read_text() == _SECRET
    assert (snapshot.path / "claude-code" / "settings.json").is_file()
    assert [h.dirname for h in snapshot.harnesses] == ["claude-code", "opencode"]
    assert snapshot.harnesses[1].entry_points == ("opencode.json",)
    assert published_snapshot(tmp_path / "runner") == snapshot.path.resolve()


def test_absolute_references_are_left_alone(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (bundle / "opencode" / "opencode.json").write_text(json.dumps({"p": f"{{file:{outside}}}"}))
    shutil.rmtree(bundle / "opencode" / "prompts")

    snapshot = publish_harness_bundle(bundle, _runtime(tmp_path))

    assert not (snapshot.path / "opencode" / "prompts").exists()
    assert sorted(p.name for p in (snapshot.path / "opencode").iterdir()) == ["opencode.json"]


def test_symlinks_are_dereferenced(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    target = tmp_path / "real.json"
    target.write_text("{}")
    (bundle / "claude-code" / "mcp.json").symlink_to(target)

    snapshot = publish_harness_bundle(bundle, _runtime(tmp_path))

    copied = snapshot.path / "claude-code" / "mcp.json"
    assert copied.is_file() and not copied.is_symlink()


# --------------------------------------------------------------------------- #
# Atomic publish.


def test_unchanged_bundle_reuses_its_snapshot(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    second = publish_harness_bundle(bundle, runtime)

    assert first.path == second.path
    assert len(list((runtime / "harness-config" / "snapshots").iterdir())) == 1
    assert list((runtime / "harness-config" / "staging").iterdir()) == []


def test_changed_bundle_publishes_a_new_snapshot_and_keeps_the_old(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    (bundle / "claude-code" / "settings.json").write_text("{}")
    second = publish_harness_bundle(bundle, runtime)

    assert first.path != second.path
    assert first.path.is_dir()
    assert published_snapshot(runtime) == second.path.resolve()


def test_failure_mid_stage_leaves_the_previous_snapshot_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    (bundle / "claude-code" / "settings.json").write_text("{}")

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(bundle_module.shutil, "copy2", explode)
    with pytest.raises(HarnessBundleError, match="disk gone"):
        publish_harness_bundle(bundle, runtime)

    assert published_snapshot(runtime) == first.path.resolve()
    assert len(list((runtime / "harness-config" / "snapshots").iterdir())) == 1
    assert list((runtime / "harness-config" / "staging").iterdir()) == []


def test_failure_swapping_current_leaves_the_previous_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path)
    first = publish_harness_bundle(bundle, runtime)
    (bundle / "claude-code" / "settings.json").write_text("{}")

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("swap failed")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(HarnessBundleError, match="swap failed"):
        publish_harness_bundle(bundle, runtime)

    assert published_snapshot(runtime) == first.path.resolve()
    assert [p.name for p in (runtime / "harness-config").iterdir() if p.name.startswith(".")] == []


# --------------------------------------------------------------------------- #
# Startup wiring.


def test_tick_with_a_malformed_bundle_exits_naming_the_path(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "claude-code" / "settings.json").write_text("{")
    runtime = _runtime(tmp_path, bundle)

    result = CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    assert result.exit_code != 0
    assert str(bundle / "claude-code" / "settings.json") in result.output
    assert "tick complete" not in result.output


def test_host_with_a_malformed_bundle_fails_before_serving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "claude-code" / "stray").write_text("x")
    runtime = _runtime(tmp_path, bundle)
    started: list[str] = []
    monkeypatch.setattr("blizzard.runner.cli.runtime.build_runner_process", lambda *a, **k: started.append("built"))

    result = CliRunner().invoke(runner_group, ["host", "--dir", str(runtime)])

    assert result.exit_code != 0
    assert str(bundle / "claude-code" / "stray") in result.output
    assert started == []


def test_host_publishes_before_building_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path, bundle)
    seen: list[Path | None] = []

    def stop(*args: object, **kwargs: object) -> None:
        seen.append(published_snapshot(runtime))
        raise RuntimeError("stop here")

    monkeypatch.setattr("blizzard.runner.cli.runtime.build_runner_process", stop)
    result = CliRunner().invoke(runner_group, ["host", "--dir", str(runtime)])

    assert isinstance(result.exception, RuntimeError)
    assert seen[0] is not None
    assert "harness config bundle" in result.output


def test_no_config_dir_creates_no_effective_directory(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    result = CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    assert not (runtime / "harness-config").exists(), result.output


def test_bundle_is_untouched_by_publish_and_init(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path, bundle)
    before = _fingerprint(bundle)

    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])
    assert published_snapshot(runtime) is not None
    assert CliRunner().invoke(runner_group, ["init", str(runtime)]).exit_code == 0

    assert _fingerprint(bundle) == before


# --------------------------------------------------------------------------- #
# `runner harness status`.


def test_status_reports_sources_and_snapshot_without_contents(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path, bundle)
    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])

    assert result.exit_code == 0, result.output
    assert str(bundle / "claude-code") in result.output
    assert "settings.json" in result.output
    assert str(published_snapshot(runtime)) in result.output
    assert "autonomy: dangerous (from [harness] autonomy)" in result.output
    assert _SECRET not in result.output


def test_status_exits_one_when_configured_but_unpublished(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, _bundle(tmp_path))

    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])

    assert result.exit_code == 1
    assert "no snapshot is published" in result.output


def test_status_without_a_bundle_names_the_generated_files(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])

    assert result.exit_code == 0, result.output
    assert "config_dir: none" in result.output
    assert "worker-settings.json" in result.output
    assert "opencode-worker-config.json" in result.output


# --------------------------------------------------------------------------- #
# Claude Code composition at publish.


def _published_settings(runtime: Path) -> dict[str, object]:
    snapshot = published_snapshot(runtime)
    assert snapshot is not None
    return json.loads((snapshot / "claude-code" / "settings.json").read_text())


def test_snapshot_settings_hold_the_operators_keys_and_the_runners_wiring(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    operator = {"model": "opus", "hooks": {"PostToolUse": [{"hooks": [{"type": "command", "command": "mine"}]}]}}
    (bundle / "claude-code" / "settings.json").write_text(json.dumps(operator))
    runtime = _runtime(tmp_path, bundle)

    assert CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)]).exit_code == 0

    settings = _published_settings(runtime)
    assert settings["model"] == "opus"
    commands = [h["command"] for g in settings["hooks"]["PostToolUse"] for h in g["hooks"]]  # type: ignore[index]
    assert commands == ["mine", "blizzard runner heartbeat"]
    assert "Monitor" in settings["permissions"]["deny"]  # type: ignore[index]
    assert settings["disableAllHooks"] is False


def test_snapshot_settings_exist_when_the_operator_supplied_none(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / "claude-code").mkdir(parents=True)
    (bundle / "claude-code" / "mcp.json").write_text("{}")
    runtime = _runtime(tmp_path, bundle)

    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    assert "SessionEnd" in _published_settings(runtime)["hooks"]  # type: ignore[operator]


@pytest.mark.parametrize("command", ["tick", "host"])
def test_a_collision_fails_startup_naming_the_native_path_and_keeps_current(tmp_path: Path, command: str) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path, bundle)
    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])
    before = published_snapshot(runtime)
    (bundle / "claude-code" / "settings.json").write_text(json.dumps({"permissions": {"allow": ["Read", "Monitor"]}}))

    result = CliRunner().invoke(runner_group, [command, "--dir", str(runtime)])

    assert result.exit_code != 0
    assert str(bundle / "claude-code" / "settings.json") in result.output
    assert "permissions.allow[1]" in result.output
    assert published_snapshot(runtime) == before


def test_the_operators_source_tree_is_byte_identical_after_composition(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    runtime = _runtime(tmp_path, bundle)
    before = _fingerprint(bundle)

    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    assert _fingerprint(bundle) == before


def test_status_prints_the_effective_settings_path_and_flags_without_contents(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    (bundle / "claude-code" / "mcp.json").write_text("{}")
    runtime = _runtime(tmp_path, bundle)
    CliRunner().invoke(runner_group, ["tick", "--dir", str(runtime)])

    result = CliRunner().invoke(runner_group, ["harness", "status", "--dir", str(runtime)])

    snapshot = published_snapshot(runtime)
    assert f"claude-code effective settings: {snapshot}/claude-code/settings.json" in result.output
    assert f"--mcp-config={snapshot}/claude-code/mcp.json" in result.output
    assert _SECRET not in result.output
