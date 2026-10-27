"""The project record in isolation — slug and name parsing, the create and edit verbs, the sole-project rule, and
reference resolution (unit tier)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.ids import Id
from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, FieldChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.kernel.id_or_slug import MatchedBy
from blizzard.hub.domain.projects.model import (
    Project,
    ProjectEdit,
    ProjectId,
    ProjectName,
    ProjectRequired,
    ProjectSlug,
    sole_project,
)
from blizzard.hub.domain.projects.resolve import ProjectMatch, locate, resolve

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 10, 9, 13, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_alice", door=Door.API)


def _project(slug: str = "winter", name: str = "Winter") -> Project:
    return Project.new(slug, name, ProjectColor.TEAL, _CTX, at=_AT)[0]


@pytest.mark.parametrize("raw", ["", "a-", "-a", "a--b", "A", "a_b", "a b", "é", "a" * 65, "a\n"])
def test_a_malformed_slug_is_refused_naming_the_field(raw: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        ProjectSlug.parse(raw)
    assert caught.value.field == "slug"
    assert ProjectSlug.of(raw) is None


@pytest.mark.parametrize("raw", ["a", "a-b-1", "0", "celestial-frontier", "a" * 64])
def test_a_well_formed_slug_parses(raw: str) -> None:
    assert ProjectSlug.parse(raw).value == raw


@pytest.mark.parametrize("raw", ["", " name", "name ", "\tname", "x" * 129])
def test_a_bad_name_is_refused_naming_the_field(raw: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        ProjectName.parse(raw)
    assert caught.value.field == "name"


@pytest.mark.parametrize("raw", ["W", "Celestial Frontier", "x" * 128, "with  inner  spaces"])
def test_a_name_of_free_text_parses(raw: str) -> None:
    assert ProjectName.parse(raw).value == raw


def test_new_mints_a_project_id_and_a_create_change_keyed_by_it() -> None:
    record, change = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT, description="the framework")
    assert Id.parse(record.project_id) is not None
    assert record.project_id.startswith("proj_")
    assert (record.slug, record.name, record.description, record.color, record.revision) == (
        "winter",
        "Winter",
        "the framework",
        ProjectColor.TEAL,
        1,
    )
    assert change.record_kind is RecordKind.PROJECT
    assert change.record_key == record.project_id
    assert change.project_id == record.project_id
    assert (change.op, change.revision, change.actor, change.door) == (ChangeOp.CREATE, 1, "usr_alice", Door.API)
    assert change.diff == (
        FieldChange("slug", None, "winter"),
        FieldChange("name", None, "Winter"),
        FieldChange("description", None, "the framework"),
        FieldChange("color", None, ProjectColor.TEAL),
    )


def test_new_refuses_a_malformed_slug_or_name() -> None:
    with pytest.raises(ConfigFieldError):
        Project.new("Winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)
    with pytest.raises(ConfigFieldError):
        Project.new("winter", " Winter", ProjectColor.TEAL, _CTX, at=_AT)


def test_two_projects_never_share_an_id() -> None:
    assert _project().project_id != _project().project_id


def test_an_edit_moves_the_revision_and_keeps_the_id() -> None:
    record = _project()
    decided = record.edit(ProjectEdit(slug="frost", name="Frost"), _CTX, if_match=1, at=_LATER)
    assert decided is not None
    edited, change = decided
    assert (edited.project_id, edited.slug, edited.name, edited.revision) == (record.project_id, "frost", "Frost", 2)
    assert (change.op, change.revision, change.record_key, change.project_id) == (
        ChangeOp.EDIT,
        2,
        record.project_id,
        record.project_id,
    )
    assert change.diff == (FieldChange("slug", "winter", "frost"), FieldChange("name", "Winter", "Frost"))
    assert record.slug_left_for(edited) == "winter"


def test_a_name_only_edit_leaves_no_slug_behind() -> None:
    record = _project()
    decided = record.edit(ProjectEdit(name="Winter framework"), _CTX, if_match=None, at=_LATER)
    assert decided is not None
    assert record.slug_left_for(decided[0]) is None


def test_an_edit_that_changes_nothing_writes_nothing() -> None:
    record = _project()
    assert record.edit(ProjectEdit(), _CTX, if_match=None, at=_LATER) is None
    assert record.edit(ProjectEdit(slug="winter", name="Winter"), _CTX, if_match=None, at=_LATER) is None


def test_a_stale_revision_is_refused_before_anything_else() -> None:
    record = _project()
    with pytest.raises(ConfigRevisionConflict) as caught:
        record.edit(ProjectEdit(), _CTX, if_match=7, at=_LATER)
    assert caught.value.current == 1


def test_an_edit_to_a_malformed_slug_is_refused() -> None:
    with pytest.raises(ConfigFieldError) as caught:
        _project().edit(ProjectEdit(slug="a--b"), _CTX, if_match=None, at=_LATER)
    assert caught.value.field == "slug"


def test_a_project_has_no_retire_or_enable_verb() -> None:
    assert not any(hasattr(Project, verb) for verb in ("retire", "enable", "set_retired"))


def test_the_sole_project_is_the_only_one() -> None:
    only = _project()
    assert sole_project([only]) is only


@pytest.mark.parametrize("count", [0, 2, 3])
def test_no_sole_project_is_required_listing_them(count: int) -> None:
    projects = [_project(f"p{n}", f"P{n}") for n in range(count)]
    with pytest.raises(ProjectRequired) as caught:
        sole_project(projects)
    assert caught.value.projects == tuple(projects)


@dataclass
class _Projects:
    """An in-memory tenant's projects and former slugs, as the store answers them."""

    current: dict[str, Project] = field(default_factory=dict)
    former: dict[str, Project] = field(default_factory=dict)

    def get(self, project_id: ProjectId) -> Project | None:
        return next((p for p in self.current.values() if p.project_id == project_id), None)

    def get_by_slug(self, slug: ProjectSlug) -> Project | None:
        return self.current.get(slug.value)

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        return self.former.get(slug.value)

    def move(self, project: Project, to: str) -> Project:
        """``project`` takes ``to``; the slug it leaves becomes former, and ``to`` is no longer former."""
        moved = replace(project, slug=to, revision=project.revision + 1, created_at=_AT)
        del self.current[project.slug]
        self.former.pop(to, None)
        self.former[project.slug] = moved
        self.current[to] = moved
        return moved


def test_an_id_resolves_whatever_the_slug() -> None:
    projects = _Projects()
    winter = _project()
    projects.current["winter"] = winter
    moved = projects.move(winter, "frost")
    match = locate(winter.project_id, projects)
    assert match is not None
    assert (match.project, match.by) == (moved, MatchedBy.ID)


def test_an_unknown_id_resolves_to_nothing() -> None:
    assert resolve(Id.mint_at("proj", _AT).value, _Projects()) is None


def test_a_current_slug_resolves_exactly() -> None:
    projects = _Projects()
    projects.current["winter"] = _project()
    match = locate("winter", projects)
    assert match is not None
    assert match.by is MatchedBy.SLUG
    assert resolve("winte", projects) is None


@pytest.mark.parametrize("value", ["Winter", "a_b", "Winter framework", " winter", "proj_short"])
def test_a_value_outside_the_slug_alphabet_resolves_to_nothing(value: str) -> None:
    projects = _Projects()
    projects.current["winter"] = _project(name="Winter")
    projects.current["winter-framework"] = _project("winter-framework", "Winter framework")
    assert resolve(value, projects) is None


def test_a_former_slug_resolves_to_the_project_that_left_it_until_it_is_taken() -> None:
    projects = _Projects()
    winter = _project()
    other = _project("other", "Other")
    projects.current.update(winter=winter, other=other)
    winter = projects.move(winter, "frost")
    match = locate("winter", projects)
    assert match is not None
    assert (match.project, match.by) == (winter, MatchedBy.FORMER_SLUG)
    other = projects.move(other, "winter")
    assert locate("winter", projects) == ProjectMatch(other, MatchedBy.SLUG)


def test_the_project_that_left_a_slug_may_take_it_back() -> None:
    projects = _Projects()
    projects.current["winter"] = _project()
    frost = projects.move(projects.current["winter"], "frost")
    back = projects.move(frost, "winter")
    match = locate("winter", projects)
    assert match is not None
    assert (match.project, match.by) == (back, MatchedBy.SLUG)
    assert locate("frost", projects) is not None
