"""Helpers for the land action tests, shared with other test modules."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.delivery.actions.land import Land
from blizzard.hub.domain.actions.effects import Effect, EffectResult, EffectStatus, LandingRecord, Marker, Pass
from blizzard.hub.domain.actions.visit import GitCommits, LandingProject, Visit, VisitChunk, VisitNode
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import (
    RepositoryId,
    RepositoryOutcome,
    RepositoryResolution,
    ResolvedRepository,
)
from tests.github_double import DoubleRepo, GitHubDouble

_WIDGET = "acme/widget"


_GADGET = "acme/gadget"


_SUBMITTED = "c" * 40


class _Sink:
    """Records every effect; a landing answers ``fenced`` once ``fence_landings`` is set, and a marker whose
    name starts with one of ``refuse`` answers ``fenced``."""

    def __init__(self, *, fence_landings: bool = False, refuse: tuple[str, ...] = ()) -> None:
        self.effects: list[Effect] = []
        self.fence_landings = fence_landings
        self._refuse = refuse

    async def record(self, effect: Effect) -> EffectResult:
        self.effects.append(effect)
        if isinstance(effect, LandingRecord) and self.fence_landings:
            return EffectResult(EffectStatus.FENCED, "the chunk is terminal or has moved past this visit")
        if isinstance(effect, Marker) and effect.name.startswith(self._refuse):
            return EffectResult(EffectStatus.FENCED, "not recorded")
        return EffectResult(EffectStatus.RECORDED)

    def landings(self) -> list[LandingRecord]:
        return [e for e in self.effects if isinstance(e, LandingRecord)]

    def markers(self) -> dict[str, str]:
        return {e.name: e.content for e in self.effects if isinstance(e, Marker)}


def _row(
    kind: ArtifactKind, name: str, data: str, *, repo: str | None = None, node_id: str = "nd_deliver"
) -> StoredArtifact:
    return StoredArtifact(
        kind=kind,
        name=name,
        data=data,
        repo=repo,
        forge=None,
        artifact_id=f"ar_{name}_{repo}",
        chunk_id="ch_1",
        node_id=node_id,
        node_name="deliver",
        epoch=1,
    )


_EXAMPLE_FORGE = ConfiguredForge(
    forge_id=ForgeId("frg_example"),
    slug="example",
    name="example",
    kind="github",
    fields=ForgeFields("https://api.github.example", None),
    revision=1,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    created_by="op",
)


def _visit(
    repos: tuple[str, ...] = (_WIDGET,),
    *,
    markers: tuple[str, ...] = (),
    expects: bool = True,
    feature_title: str | None = None,
    work_items: tuple[dict[str, str | None], ...] = (),
    public_url: str | None = None,
) -> Visit:
    commits = [
        _row(ArtifactKind.GIT_COMMIT, f"w{i}", f"feat/x:{_SUBMITTED}", repo=repo, node_id="nd_build")
        for i, repo in enumerate(repos)
    ]
    marker_rows = tuple(_row(ArtifactKind.ASSET, name, "x") for name in markers)
    targets = tuple(
        ResolvedRepository(
            forge_id=_EXAMPLE_FORGE.forge_id,
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
            action="land",
            action_args=MappingProxyType({}),
            expects_git_commits=expects,
        ),
        epoch=1,
        artifacts=(*commits, *marker_rows),
        markers=marker_rows,
        git_commits=GitCommits.of(commits),
        resolution=RepositoryResolution(
            RepositoryOutcome.RESOLVED if repos else RepositoryOutcome.NOTHING_TO_RESOLVE,
            targets=targets,
            qualified=repos,
            records=tuple(t.record for t in targets),
            forges=(_EXAMPLE_FORGE,),
        ),
        work_items=tuple(MappingProxyType(item) for item in work_items),
        feature_title=feature_title,
        public_url=public_url,
        project=LandingProject(),
        first_turn_at=datetime(2026, 10, 24, tzinfo=UTC),
        now=datetime(2026, 10, 24, tzinfo=UTC),
    )


def _forge(*repos: str, **state: Any) -> GitHubDouble:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    double = GitHubDouble()
    for repo in repos or (_WIDGET,):
        fields: dict[str, Any] = {"heads": {"feat/x": _SUBMITTED}, **state}
        double.repos[repo] = DoubleRepo(**fields)
    return double


def _land(  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    double: GitHubDouble,
    visit: Visit | None = None,
    sink: _Sink | None = None,
    offered: frozenset[Capability] = frozenset(Capability),
) -> tuple[Pass, _Sink]:
    visit = visit or _visit()
    sink = sink or _Sink()
    repos = dict(zip(visit.resolution.records, visit.resolution.qualified, strict=True))
    forges = MappingProxyType({RepositoryId(r): double.forge(r, repo, offered) for r, repo in repos.items()})
    return asyncio.run(Land().take(visit, forges, sink)), sink
