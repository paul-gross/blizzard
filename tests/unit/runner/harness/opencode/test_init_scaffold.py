"""``blizzard runner init`` scaffolds the runner-owned OpenCode plugin beside the
permission/plugin document it already wrote, and wires the two together — both
still written under the runner's own runtime root, never inside a project repository."""

from __future__ import annotations

import json
from pathlib import Path

from blizzard.runner.harness.claude_code.section import WORKER_SETTINGS_FILENAME
from blizzard.runner.harness.opencode.plugin import PLUGIN_DIRNAME, PLUGIN_FILENAME, render_plugin_source
from blizzard.runner.harness.opencode.shapes import parse_worker_config
from tests.support.config_inputs import FakeVersionProbe, host_runtime_of, init_host, selector_over
from tests.support.harness_sections import opencode, with_opencode
from tests.support.host_files import init_runner_environment, load_runner_config, write_host_file


def test_init_scaffolds_the_plugin_and_references_it_from_the_worker_config(tmp_path: Path) -> None:
    config = init_runner_environment(tmp_path)

    plugin_path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    assert plugin_path.read_text(encoding="utf-8") == render_plugin_source()

    assert opencode(config).worker_config_path is not None
    document = json.loads(opencode(config).worker_config_at(config.root).read_text())
    parsed = parse_worker_config(document)
    assert parsed.plugins == (f"file://{plugin_path}",)


def test_init_scaffolds_a_repointed_worker_config_path_not_the_default(tmp_path: Path) -> None:
    """A configured ``opencode.worker_config_path`` is where ``init`` scaffolds to, and
    the default path is left unwritten."""
    default_config = init_runner_environment(tmp_path)
    (tmp_path / "opencode-worker-config.json").unlink()
    custom_path = tmp_path / "custom" / "opencode-config.json"
    custom_path.parent.mkdir(parents=True)
    write_host_file(with_opencode(default_config, worker_config_path=str(custom_path)))

    host_runtime_of(tmp_path).init(selector_over())
    config = load_runner_config(tmp_path)

    assert opencode(config).worker_config_path == str(custom_path)
    document = json.loads(custom_path.read_text())
    parsed = parse_worker_config(document)
    assert parsed.plugins == (f"file://{tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME}",)
    assert not (tmp_path / "opencode-worker-config.json").exists()


def test_init_is_idempotent_for_the_scaffolded_plugin(tmp_path: Path) -> None:
    init_host(tmp_path)
    first = (tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME).read_text(encoding="utf-8")

    init_host(tmp_path)
    second = (tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME).read_text(encoding="utf-8")

    assert first == second


def _scaffolded(root: Path) -> dict[str, str]:
    """The text of each OpenCode file ``init`` leaves under ``root``, by relative path."""
    names = (f"{PLUGIN_DIRNAME}/{PLUGIN_FILENAME}", "opencode-worker-config.json")
    return {name: (root / name).read_text(encoding="utf-8") for name in names if (root / name).exists()}


def _clear_opencode_files(root: Path) -> None:
    (root / PLUGIN_DIRNAME / PLUGIN_FILENAME).unlink()
    (root / "opencode-worker-config.json").unlink()


def test_init_on_opencode_2_writes_neither_the_plugin_nor_the_worker_config_and_still_writes_claude_codes(
    tmp_path: Path,
) -> None:
    config = init_runner_environment(tmp_path)
    _clear_opencode_files(tmp_path)
    write_host_file(with_opencode(config, enabled=True))

    init_host(tmp_path, versions=FakeVersionProbe("opencode 2.1.4"))

    assert _scaffolded(tmp_path) == {}
    assert (tmp_path / WORKER_SETTINGS_FILENAME).is_file()


def test_init_on_opencode_1_writes_the_plugin_and_a_worker_config_that_references_it(tmp_path: Path) -> None:
    config = init_runner_environment(tmp_path)
    _clear_opencode_files(tmp_path)
    write_host_file(with_opencode(config, enabled=True))

    init_host(tmp_path, versions=FakeVersionProbe("1.18.33"))

    written = _scaffolded(tmp_path)
    assert written[f"{PLUGIN_DIRNAME}/{PLUGIN_FILENAME}"] == render_plugin_source()
    document = json.loads(written["opencode-worker-config.json"])
    assert parse_worker_config(document).plugins == (f"file://{tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME}",)


def test_init_on_opencode_2_after_opencode_1_leaves_the_opencode_1_files_unchanged(tmp_path: Path) -> None:
    config = init_runner_environment(tmp_path)
    write_host_file(with_opencode(config, enabled=True))
    init_host(tmp_path, versions=FakeVersionProbe("1.18.33"))
    before = _scaffolded(tmp_path)
    assert len(before) == 2

    init_host(tmp_path, versions=FakeVersionProbe("2.1.4"))

    assert _scaffolded(tmp_path) == before
