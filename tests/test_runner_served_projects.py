"""A runner's served declaration — how entries resolve, what adding and registering store, and what it serves —
pinned by value over an in-memory project lookup."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver
from blizzard.hub.domain.projects.model import Project, ProjectId, ProjectSlug
from blizzard.hub.domain.runners.registration import RunnerRegistration, ServedEntry
from blizzard.hub.domain.runners.served import (
    UnknownServedProject,
    declaration_at_add,
    declaration_at_registration,
    resolve_served,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _project(slug: str) -> Project:
    project_id = ProjectId(Id.mint_at(IdPrefix.PROJECT, _T0).value)
    return Project(
        project_id=project_id,
        slug=slug,
        name=slug.title(),
        description="",
        color=ProjectColor.TEAL,
        revision=1,
        created_at=_T0,
    )


@dataclass
class _Projects:
    """The tenant's projects and the slugs they left, counting each read a resolution makes."""

    held: list[Project]
    former: dict[str, Project] = field(default_factory=dict[str, Project])
    reads: list[str] = field(default_factory=list[str])

    def list_all(self) -> list[Project]:
        self.reads.append("list_all")
        return list(self.held)

    def get_by_former_slug(self, slug: ProjectSlug) -> Project | None:
        self.reads.append(f"former:{slug.value}")
        return self.former.get(slug.value)


_ALPHA = _project("alpha")
_BETA = _project("beta")


def test_an_entry_resolves_by_id_by_current_slug_and_by_a_slug_its_project_left() -> None:
    projects = _Projects([_ALPHA, _BETA], former={"old-beta": _BETA})
    served = resolve_served([_ALPHA.project_id, "beta", "old-beta"], BulkProjectResolver(projects))
    assert served == (
        ServedEntry(_ALPHA.project_id, _ALPHA.project_id),
        ServedEntry("beta", _BETA.project_id),
        ServedEntry("old-beta", _BETA.project_id),
    )


def test_an_entry_no_project_answers_resolves_to_nothing_and_a_display_name_never_resolves() -> None:
    unknown_id = Id.mint_at(IdPrefix.PROJECT, _T0).value
    served = resolve_served(["gamma", "Alpha", unknown_id], BulkProjectResolver(_Projects([_ALPHA])))
    assert served == (ServedEntry("gamma", None), ServedEntry("Alpha", None), ServedEntry(unknown_id, None))
    assert not any(entry.resolved() for entry in served)


def test_a_declaration_costs_one_bulk_read_plus_a_former_slug_lookup_only_where_nothing_current_answers() -> None:
    projects = _Projects([_ALPHA, _BETA])
    resolve_served([_ALPHA.project_id, "alpha", "beta", "gone", "Not A Slug"], BulkProjectResolver(projects))
    assert projects.reads == ["list_all", "former:gone"]


def test_an_empty_declaration_reads_nothing() -> None:
    projects = _Projects([_ALPHA])
    assert resolve_served([], BulkProjectResolver(projects)) == ()
    assert projects.reads == []


def test_a_repeated_entry_is_kept_once_in_declared_order() -> None:
    served = resolve_served(["beta", "alpha", "beta"], BulkProjectResolver(_Projects([_ALPHA, _BETA])))
    assert [entry.entry for entry in served] == ["beta", "alpha"]


def test_registering_with_a_list_replaces_the_declaration_resolving_every_entry_afresh() -> None:
    stored = (ServedEntry("alpha", _ALPHA.project_id),)
    projects = _Projects([_ALPHA, _BETA])
    assert declaration_at_registration(stored, ["beta", "gamma"], BulkProjectResolver(projects)) == (
        ServedEntry("beta", _BETA.project_id),
        ServedEntry("gamma", None),
    )


def test_registering_with_an_empty_list_clears_the_declaration() -> None:
    stored = (ServedEntry("alpha", _ALPHA.project_id),)
    projects = _Projects([_ALPHA])
    assert declaration_at_registration(stored, [], BulkProjectResolver(projects)) == ()
    assert projects.reads == []


def test_registering_with_no_list_leaves_a_resolved_declaration_untouched_and_reads_nothing() -> None:
    projects = _Projects([_ALPHA])
    assert (
        declaration_at_registration((ServedEntry("alpha", _ALPHA.project_id),), None, BulkProjectResolver(projects))
        is None
    )
    assert declaration_at_registration((), None, BulkProjectResolver(projects)) is None
    assert projects.reads == []


def test_a_resolved_entry_stays_on_its_project_when_the_slug_changes_hands() -> None:
    renamed = Project(
        project_id=_ALPHA.project_id,
        slug="alpha-two",
        name="Alpha",
        description="",
        color=ProjectColor.TEAL,
        revision=2,
        created_at=_T0,
    )
    reclaimed = _project("alpha")
    stored = (ServedEntry("alpha", _ALPHA.project_id), ServedEntry("pending", None))
    kept = declaration_at_registration(
        stored, None, BulkProjectResolver(_Projects([renamed, reclaimed, _project("pending")]))
    )
    assert kept is not None
    assert kept[0] == ServedEntry("alpha", _ALPHA.project_id)


def test_registering_with_no_list_resolves_an_unresolved_entry_once_a_project_answers_to_it() -> None:
    stored = (ServedEntry("alpha", _ALPHA.project_id), ServedEntry("gamma", None), ServedEntry("delta", None))
    gamma = _project("gamma")
    assert declaration_at_registration(stored, None, BulkProjectResolver(_Projects([_ALPHA]))) is None
    assert declaration_at_registration(stored, None, BulkProjectResolver(_Projects([_ALPHA, gamma]))) == (
        ServedEntry("alpha", _ALPHA.project_id),
        ServedEntry("gamma", gamma.project_id),
        ServedEntry("delta", None),
    )


def test_adding_with_no_list_serves_the_tenants_only_project_by_id() -> None:
    assert declaration_at_add(None, BulkProjectResolver(_Projects([_BETA]))) == (
        ServedEntry(_BETA.project_id, _BETA.project_id),
    )


def test_adding_with_no_list_serves_nothing_when_the_tenant_holds_several_projects() -> None:
    assert declaration_at_add(None, BulkProjectResolver(_Projects([_ALPHA, _BETA]))) == ()


def test_adding_with_an_empty_list_serves_nothing() -> None:
    assert declaration_at_add([], BulkProjectResolver(_Projects([_ALPHA]))) == ()


def test_adding_with_entries_stores_what_they_resolve_to() -> None:
    assert declaration_at_add(["beta", _ALPHA.project_id], BulkProjectResolver(_Projects([_ALPHA, _BETA]))) == (
        ServedEntry("beta", _BETA.project_id),
        ServedEntry(_ALPHA.project_id, _ALPHA.project_id),
    )


def test_adding_refuses_entries_no_project_answers_naming_each_of_them() -> None:
    with pytest.raises(UnknownServedProject) as refused:
        declaration_at_add(["alpha", "gamma", "Delta"], BulkProjectResolver(_Projects([_ALPHA])))
    assert refused.value.entries == ("gamma", "Delta")
    assert str(refused.value) == "no project answers to gamma, Delta"


def test_a_registration_serves_only_the_projects_its_entries_resolved_to() -> None:
    registration = RunnerRegistration(runner_id="rn_a", name="a", added_at=_T0, hub_paused=False)
    assert not registration.serves(_ALPHA.project_id)
    declared = RunnerRegistration(
        runner_id="rn_a",
        name="a",
        added_at=_T0,
        hub_paused=False,
        served=(ServedEntry("alpha", _ALPHA.project_id), ServedEntry("gamma", None)),
    )
    assert declared.serves(_ALPHA.project_id)
    assert not declared.serves(_BETA.project_id)
