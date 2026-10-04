"""The ``IWorkspaceProvider`` facts every binding answers for itself.

One parametrized suite over each binding — basic, winter, and the ``FakeProvider``
double — pinning the facts consumers read instead of comparing the binding's name:
``spawn_root``, ``capacity`` and ``pool``. A new binding joins by adding a case."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig, WorkspaceRepo
from blizzard.runner.environments.factory import WORKSPACE_PROVIDERS, build_workspace_provider
from blizzard.runner.environments.provider import IWorkspaceProvider
from tests.runner_fakes import FakeProvider


@dataclass(frozen=True)
class _Case:
    build: Callable[[Path], IWorkspaceProvider]
    spawn_root: Callable[[Path], str]
    capacity: int
    pool: tuple[str, ...]


def _config(tmp_path: Path, **fields: object) -> RunnerConfig:
    return RunnerConfig(root=tmp_path, db_url=RunnerConfig.default_db_url(tmp_path), **fields)  # type: ignore[arg-type]


_CASES = {
    "basic": _Case(
        build=lambda tmp: build_workspace_provider(
            _config(
                tmp,
                workspace_provider="basic",
                workspace_root="scratch",
                workspace_repos=(WorkspaceRepo("toy", "file:///tmp/toy.git"),),
                max_environments=4,
            )
        ),
        spawn_root=lambda _tmp: "",
        capacity=4,
        pool=(),
    ),
    "winter": _Case(
        build=lambda tmp: build_workspace_provider(
            _config(tmp, workspace_provider="winter", workspace_root=str(tmp / "ws"), workspace_envs=("a", "b", "c"))
        ),
        spawn_root=lambda tmp: str(tmp / "ws"),
        capacity=3,
        pool=("a", "b", "c"),
    ),
    # Unset, winter's spawn root stays unset rather than falling back to its working root.
    "winter-unset-root": _Case(
        build=lambda tmp: build_workspace_provider(
            _config(tmp, workspace_provider="winter", workspace_root="", workspace_envs=("a",))
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
