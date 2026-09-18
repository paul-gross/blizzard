"""``blizzard runner init`` scaffolds the runner-owned OpenCode plugin (D7, phase 4) beside the
permission/plugin document it already wrote in phase 2, and wires the two together — both
still written under the runner's own runtime root, never inside a project repository."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.opencode_plugin import PLUGIN_DIRNAME, PLUGIN_FILENAME, render_plugin_source
from blizzard.runner.harness.internal.opencode_shapes import parse_worker_config
from blizzard.runner.runtime import Runtime, init_environment

pytestmark = pytest.mark.unit


def test_init_scaffolds_the_plugin_and_references_it_from_the_worker_config(tmp_path: Path) -> None:
    config = init_environment(tmp_path)

    plugin_path = tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME
    assert plugin_path.read_text(encoding="utf-8") == render_plugin_source()

    assert config.opencode_worker_config_path is not None
    document = json.loads(Path(config.opencode_worker_config_path).read_text())
    parsed = parse_worker_config(document)
    assert parsed.plugins == (f"file://{plugin_path}",)


def test_init_scaffolds_a_repointed_worker_config_path_not_the_default(tmp_path: Path) -> None:
    """A configured ``opencode.worker_config_path`` — the same one the adapter reads at
    runtime — must be where ``init`` scaffolds to, not always the hardcoded default."""
    default_config = RunnerConfig.scaffold(tmp_path)
    custom_path = tmp_path / "custom" / "opencode-config.json"
    custom_path.parent.mkdir(parents=True)
    repointed = replace(default_config, opencode_worker_config_path=str(custom_path))
    default_config.config_path.write_text(repointed.to_toml())

    config = Runtime(tmp_path).init()

    assert config.opencode_worker_config_path == str(custom_path)
    document = json.loads(custom_path.read_text())
    parsed = parse_worker_config(document)
    assert parsed.plugins == (f"file://{tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME}",)
    assert not (tmp_path / "opencode-worker-config.json").exists()


def test_init_is_idempotent_for_the_scaffolded_plugin(tmp_path: Path) -> None:
    init_environment(tmp_path)
    first = (tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME).read_text(encoding="utf-8")

    init_environment(tmp_path)
    second = (tmp_path / PLUGIN_DIRNAME / PLUGIN_FILENAME).read_text(encoding="utf-8")

    assert first == second
