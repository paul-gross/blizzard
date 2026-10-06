"""``blizzard-runner.toml``'s ``name`` — the runner's display name, read from a toml that may
still declare the legacy ``runner_id`` (unit tier)."""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

import pytest

from blizzard.runner.config import ConfigError, RunnerConfig

pytestmark = pytest.mark.unit


def _load(root: Path, keys: str) -> RunnerConfig:
    (root / "blizzard-runner.toml").write_text(f'db_url = "sqlite:///runner.db"\n{keys}')
    return RunnerConfig.load(root)


def test_a_scaffolded_config_writes_name_and_no_runner_id(tmp_path: Path) -> None:
    emitted = tomllib.loads(RunnerConfig.scaffold(tmp_path).to_toml())

    assert emitted["name"] == "runner-local"
    assert "runner_id" not in emitted


def test_a_toml_declaring_neither_key_reads_the_default_name(tmp_path: Path) -> None:
    config = _load(tmp_path, "")

    assert config.name == "runner-local"
    assert config.shadowed_runner_id is None


def test_a_legacy_runner_id_only_toml_reads_it_as_the_name(tmp_path: Path) -> None:
    config = _load(tmp_path, 'runner_id = "r-claude"\n')

    assert config.name == "r-claude"
    assert config.shadowed_runner_id is None
    assert "runner_id" not in tomllib.loads(config.to_toml())


def test_name_wins_over_a_legacy_runner_id_declared_beside_it(tmp_path: Path) -> None:
    config = _load(tmp_path, 'name = "r-new"\nrunner_id = "r-old"\n')

    assert config.name == "r-new"
    assert config.shadowed_runner_id == "r-old"


def test_a_name_is_stripped(tmp_path: Path) -> None:
    assert _load(tmp_path, 'name = "  r-claude "\n').name == "r-claude"


@pytest.mark.parametrize(
    ("keys", "named_key"),
    [
        ('name = ""\n', "name"),
        ('name = "   "\n', "name"),
        ("name = 5\n", "name"),
        ('runner_id = ""\n', "runner_id"),
        ('name = " "\nrunner_id = "r-old"\n', "name"),
    ],
)
def test_a_blank_name_is_refused_naming_the_key_and_the_file(tmp_path: Path, keys: str, named_key: str) -> None:
    with pytest.raises(ConfigError, match=rf"^{named_key} must be a non-blank name") as refused:
        _load(tmp_path, keys)
    assert str((tmp_path / "blizzard-runner.toml").resolve()) in str(refused.value)


def test_a_name_with_toml_metacharacters_round_trips(tmp_path: Path) -> None:
    scaffold = dataclasses.replace(RunnerConfig.scaffold(tmp_path), name='r "quoted" \\ name')
    (tmp_path / "blizzard-runner.toml").write_text(scaffold.to_toml())

    assert RunnerConfig.load(tmp_path).name == 'r "quoted" \\ name'
