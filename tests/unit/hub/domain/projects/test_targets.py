"""A chunk's repository targets in isolation — the live target of each link, and the captured one taking precedence
over it (unit tier)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IReadRepositoryRecordRepository,
    RepositoryFields,
    RepositoryId,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.projects.links import IReadProjectLinks, RepositoryLink
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.domain.projects.targets import (
    BranchTarget,
    ChunkTargets,
    IWriteChunkTargets,
    TargetChunk,
    branches,
    effective_targets,
    live_targets,
    names_override,
    prepared_at,
    with_records,
)

_AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_alice", door=Door.CLI)
_PROJECT = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)[0]
_REPOSITORY = RepositoryId("repo_blizzard")


def _repository(base_branch: str = "master", *, repository_id: RepositoryId = _REPOSITORY) -> ConfiguredRepository:
    fields = RepositoryFields(ForgeId("forge_gh"), "acme", "blizzard", base_branch, "gh-token")
    return ConfiguredRepository.new("blizzard", None, fields, _CTX, at=_AT, repository_id=repository_id)[0]


def _link(base_branch: str | None = None, *, repository_id: RepositoryId = _REPOSITORY) -> RepositoryLink:
    link, _ = RepositoryLink.new(_PROJECT, repository_id, _CTX, at=_AT)
    return replace(link, base_branch=base_branch)


def test_a_link_with_no_branch_of_its_own_targets_the_repository_branch_inherited() -> None:
    targets = live_targets([_link()], {_REPOSITORY: _repository("master")})

    assert targets == {_REPOSITORY: BranchTarget(_REPOSITORY, "master", inherited=True)}


def test_a_link_naming_its_own_branch_targets_it_overridden() -> None:
    targets = live_targets([_link("rust-rewrite")], {_REPOSITORY: _repository("master")})

    assert targets == {_REPOSITORY: BranchTarget(_REPOSITORY, "rust-rewrite", inherited=False)}


def test_a_link_to_a_retired_or_missing_repository_targets_nothing() -> None:
    retired = replace(_repository(), retired=True, retired_at=_AT)
    other = RepositoryId("repo_other")

    assert live_targets([_link(), _link(repository_id=other)], {_REPOSITORY: retired}) == {}


def test_a_captured_target_outranks_the_live_one_and_the_rest_stay_live() -> None:
    other = RepositoryId("repo_other")
    captured = {_REPOSITORY: BranchTarget(_REPOSITORY, "master", inherited=True, captured_at=_AT)}
    live = {
        _REPOSITORY: BranchTarget(_REPOSITORY, "rust-rewrite", inherited=False),
        other: BranchTarget(other, "main", inherited=True),
    }

    effective = effective_targets(captured, live)

    assert effective == {_REPOSITORY: captured[_REPOSITORY], other: live[other]}
    assert branches(effective) == {_REPOSITORY: "master", other: "main"}


def test_capturing_a_target_stamps_only_its_moment() -> None:
    target = BranchTarget(_REPOSITORY, "rust-rewrite", inherited=False)

    assert target.captured(_AT) == BranchTarget(_REPOSITORY, "rust-rewrite", inherited=False, captured_at=_AT)


_INHERITED = {_REPOSITORY: BranchTarget(_REPOSITORY, "master", inherited=True)}
_OVERRIDDEN = {_REPOSITORY: BranchTarget(_REPOSITORY, "rust-rewrite", inherited=False)}


def test_only_a_links_own_branch_is_an_override() -> None:
    assert not names_override(_INHERITED)
    assert names_override(_OVERRIDDEN)


def test_environments_stand_at_targets_only_when_every_repository_names_the_same_branch() -> None:
    assert prepared_at(_OVERRIDDEN, {_REPOSITORY: "rust-rewrite"})
    assert not prepared_at(_OVERRIDDEN, {_REPOSITORY: "master"})
    assert not prepared_at(_OVERRIDDEN, {}), "a repository the runner did not prepare is not prepared at its target"
    assert not prepared_at({}, {_REPOSITORY: "master"}), "a target the chunk no longer has is stale"


def test_a_runner_naming_no_targets_stands_at_them_only_while_none_is_an_override() -> None:
    assert prepared_at(_INHERITED, None)
    assert prepared_at({}, None)
    assert not prepared_at(_OVERRIDDEN, None)


class _LinksEditedBetweenReads:
    """Project links whose branch moves after every read, as a link edit landing between two reads would."""

    def __init__(self, *branches: str) -> None:
        self._branches = list(branches)

    def repositories_of_projects(self, project_ids: Sequence[ProjectId]) -> dict[ProjectId, list[RepositoryLink]]:
        return {project_id: [_link(self._branches.pop(0))] for project_id in project_ids}


class _Records:
    def get_many(self, repository_ids: list[RepositoryId]) -> dict[RepositoryId, ConfiguredRepository]:
        return {_REPOSITORY: _repository("master")}


class _Captured:
    def __init__(self) -> None:
        self.held: dict[RepositoryId, BranchTarget] = {}

    def captured_for(
        self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None
    ) -> dict[str, dict[RepositoryId, BranchTarget]]:
        return {chunk_id: dict(self.held) for chunk_id in chunk_ids if self.held}

    def capture(
        self, chunk_id: str, targets: Sequence[BranchTarget], *, transaction: LockedTransaction
    ) -> dict[RepositoryId, BranchTarget]:
        for target in targets:
            self.held.setdefault(target.repository_id, target)
        return dict(self.held)


def _chunk_targets(store: _Captured, *branches: str) -> ChunkTargets:
    return ChunkTargets(
        targets=cast(IWriteChunkTargets, store),
        links=cast(IReadProjectLinks, _LinksEditedBetweenReads(*branches)),
        records=cast(IReadRepositoryRecordRepository, _Records()),
    )


def test_a_claim_captures_exactly_the_targets_it_checked_the_environments_against() -> None:
    store = _Captured()
    chunk = TargetChunk("chk_1", _PROJECT.project_id)
    transaction = cast(LockedTransaction, object())

    captured = _chunk_targets(store, "rust-rewrite", "edited-later").capture_prepared(
        chunk, {_REPOSITORY: "rust-rewrite"}, at=_AT, transaction=transaction
    )

    assert branches(store.held) == {_REPOSITORY: "rust-rewrite"}
    assert captured == store.held


def test_stale_environments_capture_nothing() -> None:
    store = _Captured()
    chunk = TargetChunk("chk_1", _PROJECT.project_id)
    transaction = cast(LockedTransaction, object())

    captured = _chunk_targets(store, "rust-rewrite").capture_prepared(
        chunk, {_REPOSITORY: "master"}, at=_AT, transaction=transaction
    )

    assert captured is None
    assert store.held == {}


def test_targets_pair_with_their_records_by_slug_and_one_without_a_record_is_left_out() -> None:
    other, missing = RepositoryId("repo_other"), RepositoryId("repo_missing")
    records = {_REPOSITORY: _repository(), other: replace(_repository(repository_id=other), slug="aardvark")}
    targets = {
        repository_id: BranchTarget(repository_id, "main", inherited=True)
        for repository_id in (_REPOSITORY, other, missing)
    }

    paired = with_records(targets, records)

    assert [(target.repository_id, record.slug) for target, record in paired] == [
        (other, "aardvark"),
        (_REPOSITORY, "blizzard"),
    ]
