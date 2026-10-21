"""The ``IWorkspaceProvider`` facts every binding answers for itself.

One parametrized suite over each binding — basic, winter, and the ``FakeProvider``
double — pinning the facts consumers read instead of comparing the binding's name:
``spawn_root``, ``capacity`` and ``pool``. A new binding joins by adding a case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from blizzard.runner.config import HostWorkspace
from blizzard.runner.environments.factory import WORKSPACE_PROVIDERS, WorkspaceSettings, build_workspace_provider
from blizzard.runner.environments.internal import git as env_git_module
from blizzard.runner.environments.provider import IWorkspaceProvider, WorkspaceRepo
from tests.runner_fakes import FakeProvider
from tests.support_child_launch import workspace_launch


@dataclass(frozen=True)
class _Case:
    build: Callable[[Path], IWorkspaceProvider]
    spawn_root: Callable[[Path], str]
    capacity: int
    pool: tuple[str, ...]


def _settings(tmp_path: Path, **fields: object) -> WorkspaceSettings:
    return HostWorkspace("ws", **fields).settings(tmp_path)  # type: ignore[arg-type]


_CASES = {
    "basic": _Case(
        build=lambda tmp: build_workspace_provider(
            _settings(
                tmp,
                provider="basic",
                root="scratch",
                repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
                max_environments=4,
            ),
            launch=workspace_launch(),
        ),
        spawn_root=lambda _tmp: "",
        capacity=4,
        pool=(),
    ),
    "winter": _Case(
        build=lambda tmp: build_workspace_provider(
            _settings(tmp, provider="winter", root=str(tmp / "ws"), envs=("a", "b", "c")), launch=workspace_launch()
        ),
        spawn_root=lambda tmp: str(tmp / "ws"),
        capacity=3,
        pool=("a", "b", "c"),
    ),
    # Unset, winter's spawn root stays unset rather than falling back to its working root.
    "winter-unset-root": _Case(
        build=lambda tmp: build_workspace_provider(
            _settings(tmp, provider="winter", root="", envs=("a",)), launch=workspace_launch()
        ),
        spawn_root=lambda _tmp: "",
        capacity=1,
        pool=("a",),
    ),
    "fake": _Case(
        build=lambda tmp: FakeProvider({"e1": str(tmp / "e1"), "e2": str(tmp / "e2")}),
        spawn_root=lambda _tmp: "",
        capacity=2,
        pool=("e1", "e2"),
    ),
}


@pytest.mark.unit
def test_every_registered_binding_has_a_contract_case() -> None:
    assert set(WORKSPACE_PROVIDERS) <= set(_CASES)


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_CASES))
def test_binding_answers_its_spawn_root(name: str, tmp_path: Path) -> None:
    case = _CASES[name]
    assert case.build(tmp_path).spawn_root() == case.spawn_root(tmp_path)


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_CASES))
def test_binding_answers_its_capacity(name: str, tmp_path: Path) -> None:
    case = _CASES[name]
    assert case.build(tmp_path).capacity() == case.capacity


@pytest.mark.unit
@pytest.mark.parametrize("name", list(_CASES))
def test_binding_answers_its_pool(name: str, tmp_path: Path) -> None:
    case = _CASES[name]
    assert case.build(tmp_path).pool() == case.pool


@pytest.mark.unit
def test_a_basic_binding_built_by_the_factory_honors_the_passed_held_ids_and_configured_base_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The factory hands the basic binding the caller's held-ids authority and the configured
    base branch: an environment the authority does not hold has no repos, and acquisition
    fetches the configured branch."""
    fetches: list[tuple[str, ...]] = []

    class _FetchRecordingGit:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def capture(self, cwd: Path, *args: str) -> str:
            if args[:1] == ("fetch",):
                fetches.append(args)
            if args[:2] == ("worktree", "add"):
                Path(args[-2]).mkdir(parents=True)
            return ""

    monkeypatch.setattr(env_git_module, "SubprocessEnvGit", _FetchRecordingGit)
    settings = _settings(
        tmp_path,
        provider="basic",
        root="scratch",
        repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
        base_branch="trunk",
    )
    held: list[str] = []
    provider = build_workspace_provider(settings, launch=workspace_launch(), held_ids=lambda: held)

    (acquired,) = provider.acquire("01JCHUNK", 1, [])

    assert fetches == [("fetch", "origin", "trunk")]
    assert provider.repos(acquired.environment_id) == []
    held.append(acquired.environment_id)
    assert [repo.relpath for repo in provider.repos(acquired.environment_id)] == ["toy"]
