"""A chunk's repository targets in isolation — the live target of each link, and the captured one taking precedence
over it (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields, RepositoryId
from blizzard.hub.domain.projects.links import RepositoryLink
from blizzard.hub.domain.projects.model import Project
from blizzard.hub.domain.projects.targets import RepositoryTarget, branches, effective_targets, live_targets

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

    assert targets == {_REPOSITORY: RepositoryTarget(_REPOSITORY, "master", inherited=True)}


def test_a_link_naming_its_own_branch_targets_it_overridden() -> None:
    targets = live_targets([_link("rust-rewrite")], {_REPOSITORY: _repository("master")})

    assert targets == {_REPOSITORY: RepositoryTarget(_REPOSITORY, "rust-rewrite", inherited=False)}


def test_a_link_to_a_retired_or_missing_repository_targets_nothing() -> None:
    retired = replace(_repository(), retired=True, retired_at=_AT)
    other = RepositoryId("repo_other")

    assert live_targets([_link(), _link(repository_id=other)], {_REPOSITORY: retired}) == {}


def test_a_captured_target_outranks_the_live_one_and_the_rest_stay_live() -> None:
    other = RepositoryId("repo_other")
    captured = {_REPOSITORY: RepositoryTarget(_REPOSITORY, "master", inherited=True, captured_at=_AT)}
    live = {
        _REPOSITORY: RepositoryTarget(_REPOSITORY, "rust-rewrite", inherited=False),
        other: RepositoryTarget(other, "main", inherited=True),
    }

    effective = effective_targets(captured, live)

    assert effective == {_REPOSITORY: captured[_REPOSITORY], other: live[other]}
    assert branches(effective) == {_REPOSITORY: "master", other: "main"}


def test_capturing_a_target_stamps_only_its_moment() -> None:
    target = RepositoryTarget(_REPOSITORY, "rust-rewrite", inherited=False)

    assert target.captured(_AT) == RepositoryTarget(_REPOSITORY, "rust-rewrite", inherited=False, captured_at=_AT)
