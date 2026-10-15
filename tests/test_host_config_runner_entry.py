"""A ``[[runner]]`` entry's name, id, and ``projects`` bindings: what the host reads from them, what a runner serving
nothing projects to, and what a scaffold and a round trip write."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import DEFAULT_RUNNER_NAME, SCAFFOLD_PROJECTS, ProjectBinding
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, HostConfig, HostConfigError
from tests.host_files import TEST_RUNNER_ID, write_minimal_host

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok", "BZ_HUB_TOKEN_R_CLAUDE": "tok-claude"}


def test_a_scaffold_names_the_default_runner_and_serves_default(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path)
    (entry,) = scaffold.runners
    assert (entry.name, entry.id) == (DEFAULT_RUNNER_NAME, None)
    assert tuple(binding.project for binding in entry.bindings) == SCAFFOLD_PROJECTS == ("default",)
    assert 'projects = [{ project = "default", workspace = "workspace-local", agents = 1 }]' in scaffold.to_toml()


def test_a_scaffold_naming_projects_serves_those_instead(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path, projects=("blizzard", "blizzard-context"))
    (tmp_path / "blizzard-host.toml").write_text(scaffold.to_toml())
    runner = HostConfig.load(tmp_path).runner_config(_TOKEN)
    assert runner.projects == ("blizzard", "blizzard-context")
    assert runner.bindings == (
        ProjectBinding("blizzard", "workspace-local", 1),
        ProjectBinding("blizzard-context", "workspace-local", 1),
    )


def test_a_name_is_stripped_and_a_blank_one_is_a_host_error(tmp_path: Path) -> None:
    write_minimal_host(tmp_path / "a", runner_name="  r-claude ")
    assert HostConfig.load(tmp_path / "a").sole_runner().name == "r-claude"
    for blank in ("", "   "):
        write_minimal_host(tmp_path / "b", runner_name=blank)
        with pytest.raises(HostConfigError, match="has no name"):
            HostConfig.load(tmp_path / "b")


def test_a_name_with_toml_metacharacters_round_trips(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path)
    (entry,) = scaffold.runners
    renamed = type(scaffold)(
        **{**scaffold.__dict__, "runners": (type(entry)(**{**entry.__dict__, "name": 'r "q" \\ n'}),)}
    )
    (tmp_path / "blizzard-host.toml").write_text(renamed.to_toml())
    assert HostConfig.load(tmp_path).sole_runner().name == 'r "q" \\ n'


def test_the_runners_token_variable_follows_its_name(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, runner_name="r-claude")
    (built,) = HostConfig.load(tmp_path).projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert (built.id, built.config.token_env, built.config.hub_token) == (
        TEST_RUNNER_ID,
        "BZ_HUB_TOKEN_R_CLAUDE",
        "tok-claude",
    )


@pytest.mark.parametrize(
    ("projects", "served"),
    [
        ('[{ project = "default", workspace = "workspace-local", agents = 1 }]', ("default",)),
        (
            '[{ project = "blizzard", workspace = "workspace-local", agents = 2 }, '
            '{ project = "prj_01J0000000000000000000000", workspace = "workspace-local", agents = 1 }]',
            ("blizzard", "prj_01J0000000000000000000000"),
        ),
    ],
)
def test_the_bindings_become_the_projects_served(tmp_path: Path, projects: str, served: tuple[str, ...]) -> None:
    write_minimal_host(tmp_path, projects=projects)
    runner = HostConfig.load(tmp_path).runner_config(_TOKEN)
    assert runner.projects == served


def test_a_runner_serving_nothing_projects_no_projects_and_no_agents(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, projects="[]")
    (built,) = HostConfig.load(tmp_path).projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert (built.config.projects, built.config.bindings) == ((), ())


def test_a_runner_with_no_projects_key_is_halted_naming_the_key(tmp_path: Path) -> None:
    path = write_minimal_host(tmp_path)
    path.write_text(path.read_text().replace("projects = []\n", ""))
    (halted,) = HostConfig.load(tmp_path).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner)
    assert any("projects" in error for error in halted.errors)


def test_bindings_survive_a_round_trip(tmp_path: Path) -> None:
    projects = '[{ project = "a", workspace = "workspace-local", agents = 3 }, { project = "b", workspace = "workspace-local", agents = 1 }]'
    write_minimal_host(tmp_path, projects=projects)
    loaded = HostConfig.load(tmp_path)
    (tmp_path / "blizzard-host.toml").write_text(loaded.to_toml())
    assert HostConfig.load(tmp_path).sole_runner().bindings == loaded.sole_runner().bindings
