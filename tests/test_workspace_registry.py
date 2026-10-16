"""The host's workspace registry — each declared workspace by id, and what a lease reads through it.

A lease reaches its workspace through the id its binding row names; a row naming none, or one the host does not
declare, resolves to no provider, an empty spawn root and no prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.runner.composition import bound_spawn_roots, build_workspaces
from blizzard.runner.config import HostWorkspace, ProjectBinding, RunnerConfig
from blizzard.runner.config_table import ConfigError
from blizzard.runner.environments.workspaces import (
    WorkspaceEntry,
    WorkspaceRegistry,
    held_workspace_id,
    home_workspace_id,
)
from tests.runner_fakes import FakeProvider

pytestmark = pytest.mark.unit


def _registry() -> WorkspaceRegistry:
    return WorkspaceRegistry(
        [
            WorkspaceEntry("ws_a", FakeProvider({"e1": "/a/e1"}), spawn_root="/a", prompt="A prose"),
            WorkspaceEntry("ws_b", FakeProvider({"e1": "/b/e1"}), spawn_root="", prompt="B prose"),
        ]
    )


def test_each_workspace_answers_its_own_provider_spawn_root_and_prompt() -> None:
    registry = _registry()

    assert registry.ids() == ("ws_a", "ws_b")
    assert (registry.spawn_root("ws_a"), registry.prompt("ws_a")) == ("/a", "A prose")
    assert (registry.spawn_root("ws_b"), registry.prompt("ws_b")) == ("", "B prose")
    assert registry.environment_workdirs() == {"ws_a": {"e1": "/a/e1"}, "ws_b": {"e1": "/b/e1"}}
    provider_b = registry.provider("ws_b")
    assert provider_b is not None and provider_b.environment_workdirs() == {"e1": "/b/e1"}


@pytest.mark.parametrize("workspace_id", [None, "ws_undeclared"])
def test_no_workspace_or_an_undeclared_one_resolves_to_nothing(workspace_id: str | None) -> None:
    registry = _registry()

    assert registry.provider(workspace_id) is None
    assert registry.spawn_root(workspace_id) == ""
    assert registry.prompt(workspace_id) == ""


def test_the_home_workspace_is_the_first_bound_else_the_hosts_first() -> None:
    registry = _registry()

    assert registry.home(("ws_b", "ws_a")) == "ws_b"
    assert registry.home(()) == "ws_a"
    assert home_workspace_id((), ("ws_x",)) == "ws_x"


def test_the_home_workspace_skips_a_bound_id_the_host_does_not_declare() -> None:
    registry = _registry()

    assert registry.home(("ws_undeclared", "ws_b")) == "ws_b"
    assert registry.home(("ws_undeclared",)) == "ws_a"


@pytest.mark.parametrize(
    ("held", "bound", "expected"),
    [
        ("ws_a", ("ws_b",), "ws_a"),
        (None, ("ws_b",), "ws_b"),
        ("ws_undeclared", ("ws_b",), "ws_b"),
        ("ws_undeclared", ("ws_undeclared",), "ws_a"),
        (None, (), "ws_a"),
    ],
)
def test_a_held_row_resolves_to_its_declared_workspace_else_the_runners_home(
    held: str | None, bound: tuple[str, ...], expected: str
) -> None:
    assert _registry().held(held, bound) == expected
    assert held_workspace_id(held, bound, ("ws_a", "ws_b")) == expected


def test_each_workspace_has_its_own_reentrant_acquisition_lock() -> None:
    registry = _registry()

    with registry.acquisition_lock("ws_a"), registry.acquisition_lock("ws_a"):
        assert registry.acquisition_lock("ws_a") is not registry.acquisition_lock("ws_b")


def test_a_registry_needs_a_workspace() -> None:
    with pytest.raises(ValueError, match="at least one workspace"):
        WorkspaceRegistry([])


def test_the_host_builds_one_provider_per_workspace_and_resolves_each_prompt(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("B from file")
    registry = build_workspaces(
        (
            HostWorkspace("ws_a", root=str(tmp_path / "a"), envs=("e1",), prompt="A inline"),
            HostWorkspace("ws_b", root=str(tmp_path / "b"), envs=("e1",), prompt_file="b.md"),
        ),
        tmp_path,
    )

    assert registry.ids() == ("ws_a", "ws_b")
    assert (registry.spawn_root("ws_a"), registry.spawn_root("ws_b")) == (str(tmp_path / "a"), str(tmp_path / "b"))
    assert (registry.prompt("ws_a"), registry.prompt("ws_b")) == ("A inline", "B from file")
    assert registry.provider("ws_a") is not registry.provider("ws_b")


def test_a_missing_workspace_prompt_file_fails_the_build(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="prompt_file does not exist"):
        build_workspaces((HostWorkspace("ws_a", prompt_file="absent.md"),), tmp_path)


def test_a_runner_is_addressed_by_the_first_workspace_its_bindings_name(tmp_path: Path) -> None:
    workspaces = (HostWorkspace("ws_a", root=str(tmp_path / "a")), HostWorkspace("ws_b", root=str(tmp_path / "b")))
    unbound = RunnerConfig(root=tmp_path, db_url="sqlite://", workspaces=workspaces)
    bound_to_b = RunnerConfig(
        root=tmp_path, db_url="sqlite://", workspaces=workspaces, bindings=(ProjectBinding("pr_1", "ws_b", 1),)
    )

    assert unbound.home_workspace.id == "ws_a"
    assert bound_to_b.home_workspace.id == "ws_b"
    assert bound_to_b.bound_workspaces == (workspaces[1],)
    registry = build_workspaces(workspaces, tmp_path)
    assert bound_spawn_roots(bound_to_b, registry) == (str(tmp_path / "b"),)
    assert bound_spawn_roots(unbound, registry) == (str(tmp_path / "a"),)


def test_a_runners_home_skips_a_bound_workspace_the_host_does_not_declare(tmp_path: Path) -> None:
    workspaces = (HostWorkspace("ws_a", root=str(tmp_path / "a")), HostWorkspace("ws_b", root=str(tmp_path / "b")))
    config = RunnerConfig(
        root=tmp_path,
        db_url="sqlite://",
        workspaces=workspaces,
        bindings=(ProjectBinding("pr_1", "ws_gone", 1), ProjectBinding("pr_2", "ws_b", 1)),
    )

    assert config.bound_workspace_ids == ("ws_gone", "ws_b")
    assert config.home_workspace.id == "ws_b"
