"""``SeamCommitResolver`` — the garden delivery's commit check, asked of the cited repository's ``IForgeRefs`` on the
hub's ``ActionLoop`` (unit tier). The registry is a stub handing out stub roles; the loop is real, so the check crosses
threads as it does in the effect writer."""

from __future__ import annotations

import asyncio
import importlib
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryFields,
    RepositoryId,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.garden.delivery.validation import CommitResolution, CommitResolver
from blizzard.hub.domain.projects.links import RepositoryLink
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.forge.commit_check import SeamCommitResolver
from blizzard.hub.forge.landing import (
    CommitPresence,
    Comparison,
    ForgeNotFound,
    ForgeObject,
    ForgeUnavailable,
    IForgeRefs,
)

pytestmark = pytest.mark.unit

_SHA = "deadbeef"
_PROJECT = ProjectId("widgets")
_AUTHORED = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
_EVERY = FORGE_KIND_CAPABILITIES["github"]


def _row(name: str) -> ConfiguredRepository:
    return ConfiguredRepository(
        repository_id=RepositoryId(f"repo_{name}"),
        slug=name,
        name=name,
        fields=RepositoryFields(
            forge_api_url="https://api.github.com", owner="acme", repo=name, base_branch="master", secret_name="tok"
        ),
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="op",
    )


class _Records:
    def __init__(self, rows: list[ConfiguredRepository]) -> None:
        self.rows = rows

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        return self.rows


class _Links:
    def __init__(self, linked: set[str]) -> None:
        self.linked = linked

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        return [
            RepositoryLink(project_id=project_id, repository_id=RepositoryId(f"repo_{name}"), revision=1)
            for name in sorted(self.linked)
        ]


class _Refs:
    """Answers ``commit_exists`` with ``answer``, raising it when it is an exception, or never when ``hang``."""

    def __init__(self, answer: CommitPresence | Exception, *, hang: bool = False) -> None:
        self.answer = answer
        self.hang = hang
        self.asked: list[str] = []
        self.cancelled = threading.Event()

    async def branch_tip(self, branch: str) -> str | None:
        raise NotImplementedError

    async def compare(self, base: str, head: str) -> Comparison:
        raise NotImplementedError

    async def commit_exists(self, revision: str) -> CommitPresence:
        self.asked.append(revision)
        if self.hang:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _Forges:
    def __init__(self, refs: IForgeRefs | Exception, offered: frozenset[Capability] = _EVERY) -> None:
        self._refs = refs
        self._offered = offered
        self.built: list[RepositoryId] = []

    def offered(self, forge: str, repository: RepositoryId | None = None) -> frozenset[Capability]:
        return self._offered

    def refs(self, repository: RepositoryId) -> IForgeRefs:
        self.built.append(repository)
        if isinstance(self._refs, Exception):
            raise self._refs
        return self._refs


@pytest.fixture
def loop() -> Iterator[ActionLoop]:
    action_loop = ActionLoop()
    yield action_loop
    action_loop.stop()


def _resolver(
    forges: _Forges, loop: ActionLoop, *, linked: set[str] | None = None, timeout: float = 10.0
) -> CommitResolver:
    return SeamCommitResolver(
        repositories=_Records([_row("widget")]),  # type: ignore[arg-type]
        links=_Links({"widget"} if linked is None else linked),  # type: ignore[arg-type]
        forges=forges,
        loop=loop,
        timeout=timeout,
    ).for_project(_PROJECT)


def test_a_delivered_commit_resolves_through_the_repositorys_refs(loop: ActionLoop) -> None:
    refs = _Refs(CommitPresence(exists=True, authored_at=_AUTHORED))
    forges = _Forges(refs)

    assert _resolver(forges, loop)("acme/widget", _SHA) == CommitResolution(exists=True, authored_at=_AUTHORED)
    assert forges.built == [RepositoryId("repo_widget")]
    assert refs.asked == [_SHA]


def test_a_commit_the_forge_does_not_hold_resolves_absent(loop: ActionLoop) -> None:
    assert _resolver(_Forges(_Refs(CommitPresence(exists=False))), loop)("widget", _SHA) == CommitResolution(
        exists=False
    )


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(ForgeUnavailable("down"), id="forge-unavailable"),
        pytest.param(ForgeNotFound(ForgeObject.REPOSITORY), id="repository-unseen"),
    ],
)
def test_a_forge_that_cannot_say_degrades_to_none(loop: ActionLoop, failure: Exception) -> None:
    assert _resolver(_Forges(_Refs(failure)), loop)("widget", _SHA) is None


def test_an_unrevealable_secret_degrades_to_none(loop: ActionLoop) -> None:
    forges = _Forges(RepositorySecretUnavailable("tok", retired=False))

    assert _resolver(forges, loop)("widget", _SHA) is None


def test_a_check_past_its_wait_degrades_to_none_and_cancels_the_future(loop: ActionLoop) -> None:
    refs = _Refs(CommitPresence(exists=True), hang=True)

    assert _resolver(_Forges(refs), loop, timeout=0.05)("widget", _SHA) is None
    assert refs.cancelled.wait(timeout=5)


@pytest.mark.parametrize(
    ("repo", "linked"),
    [pytest.param("gadget", None, id="no-record"), pytest.param("widget", set(), id="not-linked")],
)
def test_a_repository_no_link_resolves_degrades_to_none_without_a_forge(
    loop: ActionLoop, repo: str, linked: set[str] | None
) -> None:
    forges = _Forges(_Refs(CommitPresence(exists=True)))

    assert _resolver(forges, loop, linked=linked)(repo, _SHA) is None
    assert forges.built == []


def test_a_forge_not_offering_commits_read_degrades_to_none_without_asking(loop: ActionLoop) -> None:
    forges = _Forges(_Refs(CommitPresence(exists=True)), offered=_EVERY - {Capability.COMMITS_READ})

    assert _resolver(forges, loop)("widget", _SHA) is None
    assert forges.built == []


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("blizzard.hub.forge.internal.commit_resolver", "GitHubCommitResolver"),
        ("blizzard.hub.forge.repository_commits", "ICommitForge"),
        ("blizzard.hub.forge.repository_commits", "RepositoryCommitResolver"),
    ],
)
def test_the_direct_commit_resolvers_are_gone(module: str, name: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
    package = Path(importlib.import_module("blizzard").__file__ or "").parent
    assert not [path for path in package.rglob("*.py") if f"class {name}" in path.read_text()]
