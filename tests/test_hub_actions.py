"""``bzh:hub-action-purity``'s value gates: the catalogue and the implementations name the same set, a visit
is frozen all the way down, and every action is constructed with no arguments, so no collaborator arrives
through it."""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType, ModuleType
from typing import TypedDict, Unpack

import pytest
from typing_extensions import TypeIs

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.crash import ENV_CRASH_FENCE
from blizzard.hub.delivery.action_registry import ENV_TEST_ACTIONS, SHIPPED_ACTIONS, TEST_ACTIONS
from blizzard.hub.domain.actions.requirements import ACTION_REQUIREMENTS
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject, Visit, VisitChunk, VisitNode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import RepositoryOutcome, RepositoryResolution, ResolvedRepository
from blizzard.hub.domain.graph.actions import SHIPPED_CATALOGUE, TEST_ACTION_SPECS
from blizzard.hub.domain.projects.model import ProjectId
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_ACTIONS_DIR = repo_root() / "src" / "blizzard" / "hub" / "delivery" / "actions"


# --- the catalogue and the implementations ----------------------------------


def test_the_shipped_catalogue_and_its_implementations_name_the_same_set() -> None:
    assert SHIPPED_ACTIONS.catalogue.names == SHIPPED_CATALOGUE.names == frozenset(SHIPPED_ACTIONS.implementations)
    assert {a.spec.name for a in TEST_ACTIONS} == {spec.name for spec in TEST_ACTION_SPECS}
    for name, action in SHIPPED_ACTIONS.implementations.items():
        assert action.spec == SHIPPED_CATALOGUE.specs[name]


def test_every_admitted_action_declares_its_forge_requirements() -> None:
    assert set(ACTION_REQUIREMENTS) == SHIPPED_CATALOGUE.names | {spec.name for spec in TEST_ACTION_SPECS}


def _served_names(env: dict[str, str]) -> set[str]:
    script = (
        "from blizzard.hub.delivery.action_registry import HUB_ACTIONS; print(*sorted(HUB_ACTIONS.catalogue.names))"
    )
    clean = {k: v for k, v in os.environ.items() if k not in (ENV_TEST_ACTIONS, ENV_CRASH_FENCE)}
    out = subprocess.run(
        [sys.executable, "-c", script], env={**clean, **env}, capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


@pytest.mark.parametrize(
    ("env", "with_tests"),
    [
        ({}, False),
        ({ENV_TEST_ACTIONS: "1"}, False),
        ({ENV_CRASH_FENCE: "1"}, False),
        ({ENV_TEST_ACTIONS: "1", ENV_CRASH_FENCE: "1"}, True),
    ],
)
def test_the_test_actions_are_served_only_behind_both_fences(env: dict[str, str], with_tests: bool) -> None:
    expected = set(SHIPPED_CATALOGUE.names) | ({s.name for s in TEST_ACTION_SPECS} if with_tests else set[str]())
    assert _served_names(env) == expected


# --- a frozen visit ----------------------------------------------------------


def _is_mapping_proxy(value: object) -> TypeIs[MappingProxyType[object, object]]:
    return isinstance(value, MappingProxyType)


def _is_frozen_collection(value: object) -> TypeIs[tuple[object, ...] | frozenset[object]]:
    return isinstance(value, (tuple, frozenset))


def _mutable_parts(value: object, at: str = "visit") -> list[str]:
    """Every runtime value under ``value`` that could be mutated: a list, dict, set or bytearray, a dataclass
    instance that is not frozen, or a mapping other than a ``MappingProxyType`` over immutable values."""
    kind = type(value).__name__
    if isinstance(value, (list, dict, set, bytearray)):
        return [f"{at} is a {kind}"]
    if _is_mapping_proxy(value):
        return [p for k, v in value.items() for p in (*_mutable_parts(k, at), *_mutable_parts(v, f"{at}[{k!r}]"))]
    if _is_frozen_collection(value):
        return [p for i, item in enumerate(value) for p in _mutable_parts(item, f"{at}[{i}]")]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        frozen: bool = inspect.getattr_static(type(value), "__dataclass_params__").frozen
        found = [] if frozen else [f"{at} is a mutable {kind}"]
        return found + [
            p for f in dataclasses.fields(value) for p in _mutable_parts(getattr(value, f.name), f"{at}.{f.name}")
        ]
    return []


def _artifact(kind: ArtifactKind, name: str, data: str, *, repo: str | None = None) -> StoredArtifact:
    return StoredArtifact(
        kind=kind,
        name=name,
        data=data,
        repo=repo,
        forge=None,
        artifact_id=f"ar_{name}",
        chunk_id="ch_1",
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
    )


_FORGE = ConfiguredForge(
    forge_id=ForgeId("frg_example"),
    slug="example",
    name="example",
    kind="github",
    fields=ForgeFields("https://api.github.com", None),
    revision=1,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    created_by="op",
)


class _VisitFields(TypedDict, total=False):
    chunk: VisitChunk
    node: VisitNode
    epoch: int
    artifacts: tuple[StoredArtifact, ...]
    markers: tuple[StoredArtifact, ...]
    git_commits: GitCommits
    resolution: RepositoryResolution
    work_items: tuple[Mapping[str, str | None], ...]
    feature_title: str | None
    public_url: str | None
    project: LandingProject
    first_turn_at: datetime
    now: datetime


def _visit(**overrides: Unpack[_VisitFields]) -> Visit:
    """A visit exercising every field."""
    commit = _artifact(ArtifactKind.GIT_COMMIT, "w", f"feat/x:{'a' * 40}", repo="acme/widget")
    marker = _artifact(ArtifactKind.ASSET, "ci-rerun/1", "done")
    target = ResolvedRepository(
        forge_id=_FORGE.forge_id, owner="acme", base_branch="main", secret_name="s", record="re_1"
    )
    defaults: _VisitFields = {
        "chunk": VisitChunk(
            chunk_id="ch_1",
            project_id=ProjectId("pr_1"),
            minted_at=datetime(2026, 10, 23, tzinfo=UTC),
            work_refs=(WorkRef(source="default", ref="7"),),
        ),
        "node": VisitNode(
            node_id="nd_deliver",
            name="deliver",
            action="garden-deliver",
            action_args=MappingProxyType({"delta": ("delta",), "proposals": ()}),
            expects_git_commits=True,
        ),
        "epoch": 1,
        "artifacts": (commit, marker),
        "markers": (marker,),
        "git_commits": GitCommits.of([commit]),
        "resolution": RepositoryResolution(
            RepositoryOutcome.RESOLVED,
            targets=(target,),
            qualified=("acme/widget",),
            records=("re_1",),
            forges=(_FORGE,),
        ),
        "work_items": (MappingProxyType({"label": "default:7", "reference": None}),),
        "feature_title": "A feature",
        "public_url": "https://board.example",
        "project": LandingProject(project_id="pr_1", slug="blizzard", linked=frozenset({"re_1"})),
        "first_turn_at": datetime(2026, 10, 24, tzinfo=UTC),
        "now": datetime(2026, 10, 24, tzinfo=UTC),
    }
    return Visit(**(defaults | overrides))


def test_a_visit_is_frozen_all_the_way_down() -> None:
    assert _mutable_parts(_visit()) == []


@pytest.mark.parametrize(
    ("overrides", "found"),
    [
        ({"artifacts": [_artifact(ArtifactKind.ASSET, "a", "x")]}, "visit.artifacts is a list"),
        ({"work_items": ({"label": "x"},)}, "visit.work_items[0] is a dict"),
        (
            {
                "node": VisitNode(
                    node_id="nd", name="n", action="pass", action_args={"x": ("y",)}, expects_git_commits=False
                )
            },
            "visit.node.action_args is a dict",
        ),
    ],
)
def test_the_frozen_walk_finds_a_mutable_part(overrides: _VisitFields, found: str) -> None:
    assert found in _mutable_parts(_visit(**overrides))


def test_the_frozen_walk_finds_a_dataclass_that_is_not_frozen() -> None:
    @dataclasses.dataclass
    class Loose:
        n: int

    assert _mutable_parts(_visit(feature_title=Loose(1))) == ["visit.feature_title is a mutable Loose"]  # pyright: ignore[reportArgumentType]  # a deliberately mutable non-str title


# --- nothing smuggled in -----------------------------------------------------


def _action_classes(modules: list[ModuleType]) -> list[type]:
    """Every class a module under ``hub/delivery/actions/`` defines that takes a pass."""
    return [
        cls
        for module in modules
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__
        and inspect.iscoroutinefunction(getattr(cls, "take", None))
        and not getattr(cls, "_is_protocol", False)
    ]


def _unconstructible(classes: list[type]) -> list[str]:
    """The classes that cannot be constructed with no arguments."""
    refused: list[str] = []
    for cls in classes:
        try:
            cls()
        except TypeError:
            refused.append(cls.__name__)
    return refused


def _action_modules() -> list[ModuleType]:
    return [
        importlib.import_module(f"blizzard.hub.delivery.actions.{path.stem}")
        for path in sorted(_ACTIONS_DIR.glob("*.py"))
        if path.stem != "__init__"
    ]


def test_every_action_is_constructed_with_no_arguments() -> None:
    classes = _action_classes(_action_modules())
    assert {cls().spec.name for cls in classes} == SHIPPED_CATALOGUE.names | {s.name for s in TEST_ACTION_SPECS}
    assert _unconstructible(classes) == []


def test_the_construction_gate_refuses_an_action_handed_a_collaborator() -> None:
    class Smuggled:
        def __init__(self, store: object) -> None:
            self._store = store

        async def take(self) -> None: ...

    assert _unconstructible([Smuggled]) == ["Smuggled"]
