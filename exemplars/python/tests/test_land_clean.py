"""``land-clean`` against the in-memory GitHub double: each answer its read and merge stages give, and the landing
each merge hands over (component tier)."""

# exemplar: bzh:hub-node-step-idempotence — an action test over a recording sink, a re-run proved against its markers.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.actions.effects import Effect, EffectResult, EffectStatus, LandingRecord, Pass
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject, Visit, VisitChunk, VisitNode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.config.repositories import RepositoryOutcome, RepositoryResolution, ResolvedRepository
from exemplar.hub.delivery.actions.land_clean import LandClean
from tests.github_double import DoubleRepo, GitHubDouble, pull_body

pytestmark = pytest.mark.component

_WIDGET = "acme/widget"
_GADGET = "acme/gadget"
_SUBMITTED = "c" * 40


class _Sink:
    """Records every effect; a landing answers ``fenced`` once ``fenced`` is set."""

    def __init__(self, *, fenced: bool = False) -> None:
        self.effects: list[Effect] = []
        self._fenced = fenced

    async def record(self, effect: Effect) -> EffectResult:
        self.effects.append(effect)
        return EffectResult(EffectStatus.FENCED if self._fenced else EffectStatus.RECORDED)

    def landings(self) -> list[LandingRecord]:
        return [e for e in self.effects if isinstance(e, LandingRecord)]


def _row(kind: ArtifactKind, name: str, data: str, *, repo: str | None = None) -> StoredArtifact:
    return StoredArtifact(
        kind=kind,
        name=name,
        data=data,
        repo=repo,
        forge=None,
        artifact_id=f"ar_{name}_{repo}",
        chunk_id="ch_1",
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
    )


def _visit(*repos: str, landed: tuple[str, ...] = ()) -> Visit:
    commits = [_row(ArtifactKind.GIT_COMMIT, f"w{i}", f"feat/x:{_SUBMITTED}", repo=r) for i, r in enumerate(repos)]
    markers = tuple(_row(ArtifactKind.ASSET, f"merged/{repo}", "landed-sha") for repo in landed)
    targets = tuple(
        ResolvedRepository(
            forge_api_url="https://api.github.example",
            owner="acme",
            base_branch="main",
            secret_name="s",
            record=f"re_{repo.partition('/')[2]}",
        )
        for repo in repos
    )
    return Visit(
        chunk=VisitChunk(chunk_id="ch_1", project_id=None, minted_at=datetime(2026, 10, 23, tzinfo=UTC), work_refs=()),
        node=VisitNode(
            node_id="nd_deliver",
            name="deliver",
            action="land-clean",
            action_args=MappingProxyType({}),
            expects_git_commits=True,
        ),
        epoch=1,
        artifacts=(*commits, *markers),
        markers=markers,
        git_commits=GitCommits.of(commits),
        resolution=RepositoryResolution(
            RepositoryOutcome.RESOLVED, targets=targets, qualified=repos, records=tuple(t.record for t in targets)
        ),
        work_items=(),
        feature_title=None,
        public_url=None,
        project=LandingProject(),
    )


def _forge(*repos: str, **states: str) -> GitHubDouble:
    """Each of ``repos`` with one open pull request for ``feat/x``, reading the state ``states`` names (clean else)."""
    double = GitHubDouble()
    for number, repo in enumerate(repos, start=1):
        state: dict[str, Any] = {"mergeable_state": states.get(repo.partition("/")[2], "clean")}
        double.repos[repo] = DoubleRepo(
            heads={"feat/x": _SUBMITTED},
            pulls={number: pull_body(repo, number, branch="feat/x", sha=_SUBMITTED, base="main")},
            **state,
        )
    return double


def _land(double: GitHubDouble, visit: Visit, sink: _Sink | None = None) -> tuple[Pass, _Sink]:
    sink = sink or _Sink()
    forges = MappingProxyType({t.record: double.handle(t, "tok") for t in visit.resolution.targets})
    return asyncio.run(LandClean().take(visit, forges, sink)), sink


def test_every_clean_repository_merges_and_hands_over_one_landing_each() -> None:
    answer, sink = _land(_forge(_WIDGET, _GADGET), _visit(_WIDGET, _GADGET))

    assert answer.choice == "landed"
    assert [(landing.repository, landing.repo) for landing in sink.landings()] == [
        ("re_widget", _WIDGET),
        ("re_gadget", _GADGET),
    ]


def test_one_dirty_repository_answers_conflict_and_merges_nothing() -> None:
    double = _forge(_WIDGET, _GADGET, gadget="dirty")

    answer, sink = _land(double, _visit(_WIDGET, _GADGET))

    assert answer.choice == "conflict"
    assert double.named("merge") == []
    assert sink.effects == []


def test_an_unsettled_repository_answers_pending_and_merges_nothing() -> None:
    double = _forge(_WIDGET, widget="unknown")

    answer, _ = _land(double, _visit(_WIDGET))

    assert answer.choice == "pending"
    assert double.named("merge") == []


def test_a_re_run_skips_the_repository_its_visit_already_landed() -> None:
    double = _forge(_WIDGET, _GADGET)

    answer, sink = _land(double, _visit(_WIDGET, _GADGET, landed=(_WIDGET,)))

    assert answer.choice == "landed"
    assert [repo for repo, _ in double.named("merge")] == [_GADGET]
    assert [landing.repo for landing in sink.landings()] == [_GADGET]


def test_a_landing_the_chunk_moved_past_answers_failure_and_touches_no_other_repository() -> None:
    double = _forge(_WIDGET, _GADGET)

    answer, _ = _land(double, _visit(_WIDGET, _GADGET), _Sink(fenced=True))

    assert answer.choice == "failure"
    assert [repo for repo, _ in double.named("merge")] == [_WIDGET]
