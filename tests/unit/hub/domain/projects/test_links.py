"""A project's source and repository links in isolation — the link, unlink and relink verbs and the change each
writes, and a repository link's own base branch (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields, RepositoryId
from blizzard.hub.domain.config.work_sources import BuiltInWorkSource, WorkSourceId
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink, parse_base_branch
from blizzard.hub.domain.projects.model import Project

_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_alice", door=Door.CLI)
_PROJECT = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)[0]
_SOURCE = WorkSourceId("wsrc_gh_winter")
_REPOSITORY = RepositoryId("repo_blizzard")


def test_a_first_source_link_is_created_linked_and_keyed_by_the_source() -> None:
    link, change = SourceLink.new(_PROJECT, _SOURCE, _CTX, at=_AT)
    assert (link.project_id, link.work_source_id, link.revision, link.linked) == (
        _PROJECT.project_id,
        _SOURCE,
        1,
        True,
    )
    assert (change.record_kind, change.record_key, change.project_id) == (
        RecordKind.PROJECT_SOURCE_LINK,
        _SOURCE,
        _PROJECT.project_id,
    )
    assert (change.op, change.revision, change.actor, change.door) == (ChangeOp.CREATE, 1, "usr_alice", Door.CLI)
    assert change.diff == (FieldChange("linked", None, True),)


def test_the_built_in_hub_source_is_never_linked() -> None:
    with pytest.raises(BuiltInWorkSource):
        SourceLink.new(_PROJECT, WorkSourceId("hub"), _CTX, at=_AT)


def test_a_first_repository_link_is_created_linked_and_keyed_by_the_repository() -> None:
    link, change = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    assert (link.project_id, link.repository_id, link.revision, link.linked) == (
        _PROJECT.project_id,
        _REPOSITORY,
        1,
        True,
    )
    assert (change.record_kind, change.record_key, change.project_id, change.op) == (
        RecordKind.PROJECT_REPOSITORY_LINK,
        _REPOSITORY,
        _PROJECT.project_id,
        ChangeOp.CREATE,
    )


@pytest.mark.parametrize(
    "link",
    [
        SourceLink.new(_PROJECT, _SOURCE, _CTX, at=_AT)[0],
        RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)[0],
    ],
    ids=["source", "repository"],
)
def test_unlinking_retires_the_link_and_relinking_enables_it(link: SourceLink | RepositoryLink) -> None:
    decided = link.unlink(_CTX, if_match=1, at=_AT)
    assert decided is not None
    unlinked, change = decided
    assert (unlinked.linked, unlinked.revision) == (False, 2)
    assert (change.op, change.revision, change.diff) == (ChangeOp.RETIRE, 2, (FieldChange("linked", True, False),))
    assert change.project_id == link.project_id
    assert unlinked.unlink(_CTX, if_match=None, at=_AT) is None

    redecided = unlinked.relink(_CTX, if_match=2, at=_AT)
    assert redecided is not None
    relinked, rechange = redecided
    assert (relinked.linked, relinked.revision) == (True, 3)
    assert (rechange.op, rechange.diff) == (ChangeOp.ENABLE, (FieldChange("linked", False, True),))


def test_relinking_a_standing_link_writes_nothing() -> None:
    link = SourceLink.new(_PROJECT, _SOURCE, _CTX, at=_AT)[0]
    assert link.relink(_CTX, if_match=None, at=_AT) is None


@pytest.mark.parametrize(
    "link",
    [SourceLink.new(_PROJECT, _SOURCE, _CTX, at=_AT)[0], RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)[0]],
    ids=["source", "repository"],
)
def test_a_stale_revision_is_refused_even_for_a_no_op(link: SourceLink | RepositoryLink) -> None:
    with pytest.raises(ConfigRevisionConflict) as caught:
        link.relink(_CTX, if_match=4, at=_AT)
    assert caught.value.current == 1
    assert str(caught.value) == f"{link.LABEL} {link.project_id}/{link.target()} is at revision 1"


def test_links_of_one_record_from_two_projects_are_separate_records() -> None:
    other = Project.new("frost", "Frost", ProjectColor.TEAL, _CTX, at=_AT)[0]
    first = SourceLink.new(_PROJECT, _SOURCE, _CTX, at=_AT)[0]
    second = SourceLink.new(other, _SOURCE, _CTX, at=_AT)[0]
    assert first != second
    assert first.unlink(_CTX, if_match=None, at=_AT) is not None
    assert second.linked


def _repository(base_branch: str) -> ConfiguredRepository:
    fields = RepositoryFields(ForgeId("forge_gh"), "acme", "blizzard", base_branch, "gh-token")
    return ConfiguredRepository.new("blizzard", None, fields, _CTX, at=_AT, repository_id=_REPOSITORY)[0]


def test_a_new_repository_link_inherits_the_repository_branch() -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    assert link.base_branch is None
    assert link.effective_base_branch(_repository("master")) == "master"


def test_setting_a_base_branch_overrides_the_repository_branch_and_records_the_change() -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)

    decided = link.set_base_branch("rust-rewrite", _CTX, if_match=1, at=_AT)

    assert decided is not None
    moved, change = decided
    assert (moved.base_branch, moved.revision, moved.linked) == ("rust-rewrite", 2, True)
    assert moved.effective_base_branch(_repository("master")) == "rust-rewrite"
    assert (change.record_kind, change.record_key, change.project_id) == (
        RecordKind.PROJECT_REPOSITORY_LINK,
        _REPOSITORY,
        _PROJECT.project_id,
    )
    assert (change.op, change.revision, change.diff) == (
        ChangeOp.EDIT,
        2,
        (FieldChange("base_branch", None, "rust-rewrite"),),
    )


def test_clearing_a_base_branch_inherits_again() -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    overridden = link.set_base_branch("rust-rewrite", _CTX, if_match=None, at=_AT)
    assert overridden is not None

    decided = overridden[0].set_base_branch(None, _CTX, if_match=2, at=_AT)

    assert decided is not None
    cleared, change = decided
    assert (cleared.base_branch, cleared.revision) == (None, 3)
    assert cleared.effective_base_branch(_repository("master")) == "master"
    assert change.diff == (FieldChange("base_branch", "rust-rewrite", None),)


def test_setting_the_base_branch_the_link_has_writes_nothing() -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    assert link.set_base_branch(None, _CTX, if_match=1, at=_AT) is None
    overridden = link.set_base_branch("dev", _CTX, if_match=None, at=_AT)
    assert overridden is not None
    assert overridden[0].set_base_branch("dev", _CTX, if_match=2, at=_AT) is None


def test_a_base_branch_edit_checks_the_revision_first() -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    with pytest.raises(ConfigRevisionConflict):
        link.set_base_branch(None, _CTX, if_match=7, at=_AT)


@pytest.mark.parametrize("name", ["main", "rust-rewrite", "release/2.x", "feat/a.b", "v1.0_rc"])
def test_a_valid_branch_name_is_kept(name: str) -> None:
    assert parse_base_branch(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        " main",
        "main ",
        "has space",
        "tab\tname",
        "ctl\x01",
        "a~b",
        "a^b",
        "a:b",
        "a?b",
        "a*b",
        "a[b",
        "a\\b",
        "a..b",
        "a@{b",
        "-main",
        "/main",
        "main/",
        "a//b",
        "main.",
        "main.lock",
        "a.lock/b",
        "a/.hidden",
    ],
)
def test_a_name_that_is_no_git_branch_name_is_refused_on_base_branch(name: str) -> None:
    link, _ = RepositoryLink.new(_PROJECT, _REPOSITORY, _CTX, at=_AT)
    with pytest.raises(ConfigFieldError) as refused:
        link.set_base_branch(name, _CTX, if_match=None, at=_AT)
    assert refused.value.field == "base_branch"
