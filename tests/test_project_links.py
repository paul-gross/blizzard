"""A project's source and repository links in isolation — the link, unlink and relink verbs and the change each
writes (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.config.work_sources import BuiltInWorkSource, ConfigRevisionConflict
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import Project

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_alice", door=Door.CLI)
_PROJECT = Project.new("winter", "Winter", _CTX, at=_AT)[0]
_REPOSITORY = RepositoryId("repo_blizzard")


def test_a_first_source_link_is_created_linked_and_keyed_by_the_source() -> None:
    link, change = SourceLink.new(_PROJECT, "gh-winter", _CTX, at=_AT)
    assert (link.project_id, link.source_name, link.revision, link.linked) == (
        _PROJECT.project_id,
        "gh-winter",
        1,
        True,
    )
    assert (change.record_kind, change.record_key, change.project_id) == (
        RecordKind.PROJECT_SOURCE_LINK,
        "gh-winter",
        _PROJECT.project_id,
    )
    assert (change.op, change.revision, change.actor, change.door) == (ChangeOp.CREATE, 1, "usr_alice", Door.CLI)
    assert change.diff == (FieldChange("linked", None, True),)


def test_the_built_in_hub_source_is_never_linked() -> None:
    with pytest.raises(BuiltInWorkSource):
        SourceLink.new(_PROJECT, "hub", _CTX, at=_AT)


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
        SourceLink.new(_PROJECT, "gh-winter", _CTX, at=_AT)[0],
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
    link = SourceLink.new(_PROJECT, "gh-winter", _CTX, at=_AT)[0]
    assert link.relink(_CTX, if_match=None, at=_AT) is None


def test_a_stale_revision_is_refused_even_for_a_no_op() -> None:
    link = SourceLink.new(_PROJECT, "gh-winter", _CTX, at=_AT)[0]
    with pytest.raises(ConfigRevisionConflict) as caught:
        link.relink(_CTX, if_match=4, at=_AT)
    assert caught.value.current == 1


def test_links_of_one_record_from_two_projects_are_separate_records() -> None:
    other = Project.new("frost", "Frost", _CTX, at=_AT)[0]
    first = SourceLink.new(_PROJECT, "gh-winter", _CTX, at=_AT)[0]
    second = SourceLink.new(other, "gh-winter", _CTX, at=_AT)[0]
    assert first != second
    assert first.unlink(_CTX, if_match=None, at=_AT) is not None
    assert second.linked
