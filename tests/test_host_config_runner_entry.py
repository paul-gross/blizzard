"""A ``[[runner]]`` entry's name, id, and ``projects`` bindings: what the host reads from them, what a runner serving
nothing projects to, and what a scaffold and a round trip write."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.config import DEFAULT_RUNNER_NAME, SCAFFOLD_PROJECTS, ProjectBinding
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, HostConfig, HostConfigError
from tests import harness_sections
from tests.config_inputs import load_host_config
from tests.host_files import TEST_RUNNER_ID, write_minimal_host

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok", "BZ_HUB_TOKEN_R_CLAUDE": "tok-claude"}


def test_a_scaffold_names_the_default_runner_and_serves_default(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path)
    (entry,) = scaffold.runners
    assert (entry.name, entry.id) == (DEFAULT_RUNNER_NAME, None)
    assert tuple(binding.project for binding in entry.bindings) == SCAFFOLD_PROJECTS == ("default",)
    assert 'projects = [{ project = "default", workspace = "workspace-local" }]' in scaffold.to_toml()
    assert "agents = 1\n" in scaffold.to_toml()


def test_a_scaffold_naming_projects_serves_those_instead(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path, projects=("blizzard", "blizzard-context"))
    (tmp_path / "blizzard-host.toml").write_text(scaffold.to_toml())
    runner = load_host_config(tmp_path).runner_config(_TOKEN)
    assert runner.projects == ("blizzard", "blizzard-context")
    assert runner.bindings == (
        ProjectBinding("blizzard", "workspace-local"),
        ProjectBinding("blizzard-context", "workspace-local"),
    )
    assert runner.agents == 1


def test_a_name_is_stripped_and_a_blank_one_is_a_host_error(tmp_path: Path) -> None:
    write_minimal_host(tmp_path / "a", runner_name="  r-claude ")
    assert load_host_config(tmp_path / "a").sole_runner().name == "r-claude"
    for blank in ("", "   "):
        write_minimal_host(tmp_path / "b", runner_name=blank)
        with pytest.raises(HostConfigError, match="has no name"):
            load_host_config(tmp_path / "b")


def test_a_name_with_toml_metacharacters_round_trips(tmp_path: Path) -> None:
    scaffold = HostConfig.scaffold(tmp_path)
    (entry,) = scaffold.runners
    renamed = type(scaffold)(
        **{**scaffold.__dict__, "runners": (type(entry)(**{**entry.__dict__, "name": 'r "q" \\ n'}),)}
    )
    (tmp_path / "blizzard-host.toml").write_text(renamed.to_toml())
    assert load_host_config(tmp_path).sole_runner().name == 'r "q" \\ n'


def test_the_runners_token_variable_follows_its_name(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, runner_name="r-claude")
    (built,) = load_host_config(tmp_path).projections(_TOKEN)
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
    runner = load_host_config(tmp_path).runner_config(_TOKEN)
    assert runner.projects == served


def test_a_runner_serving_nothing_projects_no_projects_and_no_agents(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, projects="[]")
    (built,) = load_host_config(tmp_path).projections(_TOKEN)
    assert isinstance(built, BuiltRunner)
    assert (built.config.projects, built.config.bindings) == ((), ())


def test_a_runner_with_no_projects_key_is_halted_naming_the_key(tmp_path: Path) -> None:
    path = write_minimal_host(tmp_path)
    path.write_text(path.read_text().replace("projects = []\n", ""))
    (halted,) = load_host_config(tmp_path).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner)
    assert any("projects" in error for error in halted.errors)


def test_bindings_survive_a_round_trip(tmp_path: Path) -> None:
    projects = '[{ project = "a", workspace = "workspace-local", agents = 3 }, { project = "b", workspace = "workspace-local", agents = 1 }]'
    write_minimal_host(tmp_path, projects=projects)
    loaded = load_host_config(tmp_path)
    (tmp_path / "blizzard-host.toml").write_text(loaded.to_toml())
    reloaded = load_host_config(tmp_path)
    assert [(b.project, b.workspace) for b in reloaded.sole_runner().bindings] == [
        ("a", "workspace-local"),
        ("b", "workspace-local"),
    ]
    (reloaded_own, loaded_own) = (reloaded.sole_runner().own, loaded.sole_runner().own)
    assert reloaded_own is not None and loaded_own is not None
    assert reloaded_own.agents == loaded_own.agents == 4


def _agents_host(tmp_path: Path, body: str) -> HostConfig:
    write_minimal_host(tmp_path, runner_name="r-claude")
    text = (tmp_path / "blizzard-host.toml").read_text()
    start = text.index("[[runner]]")
    head = text[:start]
    (tmp_path / "blizzard-host.toml").write_text(head + '[[runner]]\nid = "rn_x"\nname = "r-claude"\n' + body)
    return load_host_config(tmp_path)


_BOTH = 'projects = [{ project = "a", workspace = "workspace-local", agents = 2 }, { project = "b", workspace = "workspace-local", agents = 3 }]\n'
_BARE = (
    'projects = [{ project = "a", workspace = "workspace-local" }, { project = "b", workspace = "workspace-local" }]\n'
)


def test_an_unset_runner_count_is_the_sum_of_the_bindings_and_warns_deprecated(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, _BOTH)
    (entry,) = host.runners
    assert entry.own is not None and entry.own.agents == 5
    assert any("deprecated" in warning for warning in host.warnings)


def test_a_runner_count_wins_over_the_bindings_and_warns_they_are_ignored(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, "agents = 4\n" + _BOTH)
    (entry,) = host.runners
    assert entry.own is not None and entry.own.agents == 4
    assert [w for w in host.warnings if "ignored" in w] and len([w for w in host.warnings if "r-claude" in w]) == 1


def test_a_runner_count_with_bare_bindings_is_quiet(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, "agents = 4\n" + _BARE)
    assert host.runners[0].warnings == ()
    assert host.runner_config(_TOKEN).agents == 4


def test_no_count_and_a_bare_binding_halts_the_runner(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, _BARE)
    assert "has no agents" in "; ".join(host.runners[0].errors)


def test_a_runner_serving_nothing_with_no_count_has_none(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, "projects = []\n")
    assert host.runners[0].own is not None and host.runners[0].own.agents == 0


def test_both_harness_sections_parse_their_supported_models(tmp_path: Path) -> None:
    host = _agents_host(
        tmp_path,
        'agents = 1\nprojects = []\n[runner.models]\nsupported = ["opus", "claude-*"]\n'
        '[runner.opencode.models]\nsupported = ["openai/*"]\n',
    )
    config = host.runner_config(_TOKEN)
    assert harness_sections.claude_code(config).supported_models == ("opus", "claude-*")
    assert harness_sections.opencode(config).supported_models == ("openai/*",)


def test_an_unset_supported_list_parses_as_the_harness_default(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, "agents = 1\nprojects = []\n")
    config = host.runner_config(_TOKEN)
    assert harness_sections.claude_code(config).supported_models is None
    assert harness_sections.opencode(config).supported_models is None


def test_an_opencode_supported_entry_without_a_slash_halts_the_runner_naming_it(tmp_path: Path) -> None:
    host = _agents_host(tmp_path, 'agents = 1\nprojects = []\n[runner.opencode.models]\nsupported = ["sol"]\n')
    with pytest.raises(HostConfigError, match=r"runner r-claude: .*'sol'"):
        host.runner_config(_TOKEN)
