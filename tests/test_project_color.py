"""A project's color in isolation — the palette refusal, the least-used choice, the edit's diff, and the colors an
apply gives the projects it creates (unit tier)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.apply import ApplyEntryRefused
from blizzard.hub.domain.config.changes import ChangeContext, Door, FieldChange
from blizzard.hub.domain.config.work_sources import ConfigFieldError
from blizzard.hub.domain.projects.color import least_used, parse_color
from blizzard.hub.domain.projects.declarations import ProjectDeclaration, StoredProjects, plan_projects
from blizzard.hub.domain.projects.model import Project, ProjectEdit

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="usr_alice", door=Door.API)
_PALETTE = ("red", "orange", "amber", "lime", "green", "teal", "cyan", "blue", "indigo", "violet", "pink", "slate")


class _Recording:
    """A pick that records the candidates it was offered and takes the last of them."""

    def __init__(self) -> None:
        self.offered: list[tuple[ProjectColor, ...]] = []

    def __call__(self, colors: Sequence[ProjectColor]) -> ProjectColor:
        self.offered.append(tuple(colors))
        return colors[-1]


def _first(colors: Sequence[ProjectColor]) -> ProjectColor:
    return colors[0]


def test_the_palette_is_twelve_names_in_order() -> None:
    assert tuple(color.value for color in ProjectColor) == _PALETTE


def test_a_palette_name_parses() -> None:
    assert parse_color("teal") is ProjectColor.TEAL


def test_a_hex_color_parses_lowercased() -> None:
    assert parse_color("#14B8a6") == "#14b8a6"


@pytest.mark.parametrize("raw", ["mauve", "", "Teal", " teal", "#abc", "#12345g", "14b8a6", "#14b8a6ff", "rgb(1,2,3)"])
def test_a_value_that_is_neither_a_palette_name_nor_a_hex_is_refused_on_color_naming_both(raw: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        parse_color(raw)
    assert caught.value.field == "color"
    assert caught.value.message == f"must be one of {', '.join(_PALETTE)}, or a #rrggbb hex color, got {raw!r}"


def test_a_held_hex_color_counts_toward_no_palette_color() -> None:
    pick = _Recording()
    assert least_used(["#14b8a6", "#ff8800"], pick) is ProjectColor.SLATE
    assert pick.offered == [tuple(ProjectColor)]


def test_with_nothing_held_every_color_is_offered_in_palette_order() -> None:
    pick = _Recording()
    assert least_used((), pick) is ProjectColor.SLATE
    assert pick.offered == [tuple(ProjectColor)]


def test_only_the_least_held_colors_are_offered() -> None:
    held = [color for color in ProjectColor if color not in (ProjectColor.AMBER, ProjectColor.PINK)]
    held += [ProjectColor.RED]
    pick = _Recording()

    assert least_used(held, pick) is ProjectColor.PINK
    assert pick.offered == [(ProjectColor.AMBER, ProjectColor.PINK)]


def test_when_every_color_is_held_the_ones_held_least_are_offered() -> None:
    held = [*ProjectColor, *ProjectColor, ProjectColor.BLUE]
    pick = _Recording()
    least_used(held, pick)
    assert pick.offered == [tuple(c for c in ProjectColor if c is not ProjectColor.BLUE)]


def test_an_edit_of_the_color_bumps_the_revision_and_names_color_in_the_diff() -> None:
    record = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)[0]

    decided = record.edit(ProjectEdit(color="pink"), _CTX, if_match=None, at=_AT)

    assert decided is not None
    edited, change = decided
    assert (edited.color, edited.revision) == (ProjectColor.PINK, 2)
    assert change.diff == (FieldChange("color", ProjectColor.TEAL, ProjectColor.PINK),)


def test_an_edit_to_the_same_color_writes_nothing() -> None:
    record = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)[0]
    assert record.edit(ProjectEdit(color="teal"), _CTX, if_match=None, at=_AT) is None


def test_an_edit_to_an_off_palette_color_is_refused_on_color() -> None:
    record = Project.new("winter", "Winter", ProjectColor.TEAL, _CTX, at=_AT)[0]
    with pytest.raises(ConfigFieldError) as caught:
        record.edit(ProjectEdit(color="mauve"), _CTX, if_match=None, at=_AT)
    assert caught.value.field == "color"


def _stored(*projects: Project) -> StoredProjects:
    return StoredProjects(projects=projects)


def test_an_apply_gives_each_created_project_the_color_the_earlier_entries_left_least_used() -> None:
    held = Project.new("held", "Held", ProjectColor.RED, _CTX, at=_AT)[0]
    declared = [
        ProjectDeclaration("a", ProjectEdit(name="A")),
        ProjectDeclaration("b", ProjectEdit(name="B", color="amber")),
        ProjectDeclaration("c", ProjectEdit(name="C")),
        ProjectDeclaration("d", ProjectEdit(name="D")),
    ]

    plan = plan_projects(declared, _stored(held), _CTX, pick=_first, at=_AT)

    colors = {project.slug: project.color for project in plan.projects}
    assert colors == {
        "held": ProjectColor.RED,
        "a": ProjectColor.ORANGE,
        "b": ProjectColor.AMBER,
        "c": ProjectColor.LIME,
        "d": ProjectColor.GREEN,
    }


def test_an_apply_entry_stating_an_off_palette_color_is_refused() -> None:
    declared = [ProjectDeclaration("a", ProjectEdit(name="A", color="mauve"))]
    with pytest.raises(ApplyEntryRefused):
        plan_projects(declared, _stored(), _CTX, pick=_first, at=_AT)
