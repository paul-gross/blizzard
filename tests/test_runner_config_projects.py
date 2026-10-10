"""The runner configuration's ``projects`` — the projects a runner serves, by id or slug. An absent key
is ``None`` (declare nothing: the hub keeps what it holds), never ``()``, which is an explicit
``projects = []`` serving none; each shape survives ``to_toml`` and ``load``, and a fresh scaffold
serves ``default``."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import CONFIG_FILENAME, SCAFFOLD_PROJECTS, ConfigError, RunnerConfig

pytestmark = pytest.mark.unit


def _load(root: Path, body: str) -> RunnerConfig:
    (root / CONFIG_FILENAME).write_text(f'db_url = "sqlite:///{root / "runner.db"}"\n{body}')
    return RunnerConfig.load(root)


@pytest.mark.parametrize(
    ("body", "projects"),
    [
        ("", None),
        ("projects = []\n", ()),
        ('projects = ["default"]\n', ("default",)),
        ('projects = ["blizzard", "prj_01J0000000000000000000000"]\n', ("blizzard", "prj_01J0000000000000000000000")),
    ],
)
def test_load_tells_an_absent_key_from_an_empty_list_from_a_list(
    tmp_path: Path, body: str, projects: tuple[str, ...] | None
) -> None:
    assert _load(tmp_path, body).projects == projects


@pytest.mark.parametrize("value", ['"default"', "[1]", '[""]', '["ok", 2]'])
def test_load_refuses_projects_that_are_not_a_list_of_names(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigError, match="projects must be a list"):
        _load(tmp_path, f"projects = {value}\n")


@pytest.mark.parametrize("projects", [None, (), ("default",), ("blizzard", "blizzard-context")])
def test_to_toml_round_trips_each_shape(tmp_path: Path, projects: tuple[str, ...] | None) -> None:
    written = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", projects=projects)
    (tmp_path / CONFIG_FILENAME).write_text(written.to_toml())
    assert RunnerConfig.load(tmp_path).projects == projects


def test_to_toml_writes_no_key_for_a_config_naming_none(tmp_path: Path) -> None:
    toml = RunnerConfig(root=tmp_path, db_url="sqlite:///x.db").to_toml()
    assert not [line for line in toml.splitlines() if "projects =" in line]


def test_a_fresh_scaffold_serves_default(tmp_path: Path) -> None:
    scaffolded = RunnerConfig.scaffold(tmp_path)
    assert scaffolded.projects == SCAFFOLD_PROJECTS == ("default",)
    assert 'projects = ["default"]' in scaffolded.to_toml().splitlines()


def test_a_scaffold_naming_projects_serves_those_instead(tmp_path: Path) -> None:
    scaffolded = RunnerConfig.scaffold(tmp_path, projects=("blizzard", "blizzard-context"))
    (tmp_path / CONFIG_FILENAME).write_text(scaffolded.to_toml())
    assert RunnerConfig.load(tmp_path).projects == ("blizzard", "blizzard-context")
