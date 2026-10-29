"""``ProjectStore`` and ``ProjectLinkStore`` over a migrated store, driven through ``ProjectAuthoring`` — slugs and
former slugs, the compare-and-set, link facts and the change row each write commits (component tier)."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryDraft
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceDraft, WorkSourceId
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.model import Project, ProjectEdit, ProjectId, ProjectSlug, ProjectSlugTaken
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.former_slugs import SlugTables
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.schema import config_changes, project_former_slugs, project_source_link_facts
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import (
    OP,
    add_tenant,
    config_authoring,
    default_project,
    default_tenant,
    first_color,
    hub_migration_prototype,
    link_project,
    scoped_connections,
    seed_project,
)

pytestmark = pytest.mark.component

_T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class _World:
    """One migrated store; ``tenant`` is its carried-over tenant, holding its ``default`` project."""

    def __init__(self, tmp_path: Path) -> None:
        shutil.copy(hub_migration_prototype(), tmp_path / "hub.db")
        self.engine: Engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
        self.tmp_path = tmp_path
        self.clock = FixedClock(_T0)
        self.tenant = default_tenant(self.engine)
        self.default = self.projects().get(ProjectId(default_project(self.engine)))
        self.config().create_secret(SecretName.parse("forge-token"), "tok", OP)

    def conns(self, tenant: TenantId | None = None) -> ScopedConnections:
        return scoped_connections(self.engine, tenant if tenant is not None else self.tenant)

    def projects(self, tenant: TenantId | None = None) -> ProjectStore:
        return ProjectStore(self.conns(tenant))

    def links(self, tenant: TenantId | None = None) -> ProjectLinkStore:
        return ProjectLinkStore(self.conns(tenant))

    def authoring(self, tenant: TenantId | None = None) -> ProjectAuthoring:
        links = self.links(tenant)
        return ProjectAuthoring(
            projects=self.projects(tenant),
            links=links,
            positions=ProjectPositionStore(self.conns(tenant), links=links),
            pick=first_color,
            clock=self.clock,
        )

    def config(self) -> ConfigAuthoring:
        keys = hub_key_provider({}, data_dir=self.tmp_path / "data")
        return config_authoring(self.engine, keys=keys, clock=self.clock)

    def changes(self, kind: RecordKind, key: str) -> list[ConfigChange]:
        page = ConfigChangeStore(self.conns()).page(before=None, limit=100, record_kind=kind, record_key=key)
        return list(reversed(page))

    def source(self, name: str) -> ConfiguredWorkSource:
        draft = WorkSourceDraft(provider="github", locator=f"acme/{name}", annotate=False, secret="forge-token")
        return self.config().create_work_source_naming(name, None, draft, ForgeChoice(), OP).record

    def repository(self, name: str) -> ConfiguredRepository:
        draft = RepositoryDraft(owner="acme", repo=name, base_branch="main", secret_name="forge-token")
        choice = ForgeChoice(api_base="https://api.github.com")
        return self.config().create_repository_naming(name, None, draft, choice, OP).record


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _slug(raw: str) -> ProjectSlug:
    return ProjectSlug.parse(raw)


def _former_rows(world: _World) -> list[tuple[str, str]]:
    table = project_former_slugs
    with world.engine.connect() as conn:
        rows = conn.execute(world.conns().select(table, table.c.slug, table.c.project_id).order_by(table.c.slug))
        return [(row.slug, row.project_id) for row in rows]


# --- projects ------------------------------------------------------------------------------


def test_a_created_project_reads_back_and_commits_its_create_change_in_its_project(world: _World) -> None:
    project = world.authoring().create("winter", "Winter", OP, description="the cold one")
    assert world.projects().get(project.project_id) == project
    assert world.projects().get_by_slug(_slug("winter")) == project
    assert [p.slug for p in world.projects().list_all()] == ["default", "winter"]
    [change] = world.changes(RecordKind.PROJECT, project.project_id)
    assert (change.op, change.revision, change.project_id) == (ChangeOp.CREATE, 1, project.project_id)


def test_a_stated_color_round_trips_through_create_and_edit(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP, color="teal")
    assert world.projects().get(created.project_id) == created
    assert created.color is ProjectColor.TEAL

    edited = world.authoring().edit(created, ProjectEdit(color="pink"), OP)

    stored = world.projects().get(created.project_id)
    assert stored == edited
    assert stored is not None
    assert (stored.color, stored.revision) == (ProjectColor.PINK, 2)


def test_a_hex_color_round_trips_lowercase_through_create_and_edit(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP, color="#14B8A6")
    assert world.projects().get(created.project_id) == created
    assert created.color == "#14b8a6"

    edited = world.authoring().edit(created, ProjectEdit(color="#FF8800"), OP)

    stored = world.projects().get(created.project_id)
    assert stored == edited
    assert stored is not None
    assert (stored.color, stored.revision) == ("#ff8800", 2)


def test_a_create_stating_no_color_takes_the_tenants_least_used_one(world: _World) -> None:
    held = {project.color for project in world.projects().list_all()}

    created = world.authoring().create("winter", "Winter", OP)

    assert created.color == next(color for color in ProjectColor if color not in held)
    assert world.projects().get(created.project_id) == created


def test_a_slug_another_project_holds_is_refused(world: _World) -> None:
    with pytest.raises(ProjectSlugTaken):
        world.authoring().create("default", "Second default", OP)
    assert [p.slug for p in world.projects().list_all()] == ["default"]


def test_a_slug_taken_by_a_concurrent_write_is_refused_by_the_unique(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SlugTables, "refuse_held", lambda self, conn, record_id, slug: None)
    with pytest.raises(ProjectSlugTaken):
        world.authoring().create("default", "Raced", OP)


def test_two_tenants_each_hold_the_same_slug(world: _World) -> None:
    other = add_tenant(world.engine, "other")
    mine = world.authoring().create("winter", "Winter", OP)
    theirs = world.authoring(other).create("winter", "Winter", OP)
    assert mine.project_id != theirs.project_id
    assert world.projects(other).get_by_slug(_slug("winter")) == theirs
    assert world.projects().get(theirs.project_id) is None


def test_two_projects_may_share_a_display_name(world: _World) -> None:
    world.authoring().create("one", "Same", OP)
    world.authoring().create("two", "Same", OP)
    assert [p.name for p in world.projects().list_all()] == ["Default", "Same", "Same"]


def test_a_rename_keeps_the_id_and_records_the_slug_it_left(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    world.clock.advance(timedelta(minutes=1))
    renamed = world.authoring().edit(created, ProjectEdit(slug="summer"), OP, if_match=1)
    assert (renamed.project_id, renamed.slug, renamed.revision) == (created.project_id, "summer", 2)
    assert world.projects().get_by_former_slug(_slug("winter")) == renamed
    assert world.projects().resolve("winter") == renamed
    assert world.projects().get_by_slug(_slug("winter")) is None
    assert _former_rows(world) == [("winter", created.project_id)]
    ops = [(c.op, c.revision) for c in world.changes(RecordKind.PROJECT, created.project_id)]
    assert ops == [(ChangeOp.CREATE, 1), (ChangeOp.EDIT, 2)]


def test_a_former_slug_taken_by_a_new_project_resolves_to_its_new_holder(world: _World) -> None:
    leaver = world.authoring().create("winter", "Winter", OP)
    world.authoring().edit(leaver, ProjectEdit(slug="summer"), OP)
    holder = world.authoring().create("winter", "New winter", OP)
    assert world.projects().resolve("winter") == holder
    assert world.projects().get_by_former_slug(_slug("winter")) is None
    assert _former_rows(world) == []


def test_a_project_leaving_a_former_slug_replaces_its_earlier_holder(world: _World) -> None:
    first = world.authoring().create("winter", "Winter", OP)
    world.authoring().edit(first, ProjectEdit(slug="summer"), OP)
    second = world.authoring().create("winter", "New winter", OP)
    moved = world.authoring().edit(second, ProjectEdit(slug="autumn"), OP)
    assert world.projects().get_by_former_slug(_slug("winter")) == moved
    assert _former_rows(world) == [("winter", second.project_id)]


def test_the_leaver_may_take_its_former_slug_back(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    renamed = world.authoring().edit(created, ProjectEdit(slug="summer"), OP)
    back = world.authoring().edit(renamed, ProjectEdit(slug="winter"), OP)
    assert world.projects().get_by_slug(_slug("winter")) == back
    assert world.projects().get_by_former_slug(_slug("summer")) == back
    assert _former_rows(world) == [("summer", created.project_id)]


def test_an_edit_that_moves_no_slug_writes_no_former_slug(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    world.authoring().edit(created, ProjectEdit(name="Winter, renamed", description="cold"), OP)
    assert world.projects().get(created.project_id) == Project(
        project_id=created.project_id,
        slug="winter",
        name="Winter, renamed",
        description="cold",
        color=created.color,
        revision=2,
        created_at=_T0,
    )
    assert _former_rows(world) == []


def test_an_edit_from_a_moved_revision_is_refused_and_writes_nothing(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    world.authoring().edit(created, ProjectEdit(name="First"), OP)
    with pytest.raises(ConfigRevisionConflict):
        world.authoring().edit(created, ProjectEdit(slug="summer"), OP)
    assert world.projects().get_by_slug(_slug("winter")) is not None
    assert _former_rows(world) == []
    assert len(world.changes(RecordKind.PROJECT, created.project_id)) == 2


def test_a_rename_onto_a_slug_another_project_holds_is_refused(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    with pytest.raises(ProjectSlugTaken):
        world.authoring().edit(created, ProjectEdit(slug="default"), OP)
    assert world.projects().get(created.project_id) == created


def test_get_many_answers_only_the_projects_that_exist(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    other = add_tenant(world.engine, "other")
    foreign = world.authoring(other).create("summer", "Summer", OP)
    found = world.projects().get_many([created.project_id, foreign.project_id])
    assert found == {created.project_id: created}


# --- links ---------------------------------------------------------------------------------


def _source_facts(world: _World) -> list[tuple[str, str, bool]]:
    facts = project_source_link_facts
    with world.engine.connect() as conn:
        rows = conn.execute(
            world.conns().select(facts, facts.c.project_id, facts.c.work_source_id, facts.c.linked).order_by(facts.c.id)
        )
        return [(row.project_id, row.work_source_id, row.linked) for row in rows]


def test_a_first_source_link_writes_the_link_its_fact_and_its_change(world: _World) -> None:
    source = world.source("gh-winter")
    assert world.default is not None
    link = world.authoring().link_source(world.default, source, OP)
    assert world.links().sources_of(world.default.project_id) == [link]
    assert world.links().source_link(world.default.project_id, source.work_source_id) == link
    assert _source_facts(world) == [(world.default.project_id, source.work_source_id, True)]
    [change] = world.changes(RecordKind.PROJECT_SOURCE_LINK, source.work_source_id)
    assert (change.op, change.revision, change.project_id) == (ChangeOp.CREATE, 1, world.default.project_id)


def test_unlinking_and_relinking_move_the_revision_and_append_a_fact_each(world: _World) -> None:
    source = world.source("gh-winter")
    assert world.default is not None
    project = world.default
    link = world.authoring().link_source(project, source, OP)
    unlinked = world.authoring().unlink_source(link, OP, if_match=1)
    assert (unlinked.revision, unlinked.linked) == (2, False)
    assert world.links().sources_of(project.project_id) == []
    assert world.links().source_link(project.project_id, source.work_source_id) == unlinked
    relinked = world.authoring().link_source(project, source, OP, if_match=2)
    assert (relinked.revision, relinked.linked) == (3, True)
    assert [linked for _, _, linked in _source_facts(world)] == [True, False, True]
    ops = [(c.op, c.revision) for c in world.changes(RecordKind.PROJECT_SOURCE_LINK, source.work_source_id)]
    assert ops == [(ChangeOp.CREATE, 1), (ChangeOp.RETIRE, 2), (ChangeOp.ENABLE, 3)]


def test_a_standing_link_or_a_repeated_unlink_writes_nothing(world: _World) -> None:
    source = world.source("gh-winter")
    assert world.default is not None
    link = world.authoring().link_source(world.default, source, OP)
    assert world.authoring().link_source(world.default, source, OP) == link
    unlinked = world.authoring().unlink_source(link, OP)
    assert world.authoring().unlink_source(unlinked, OP) == unlinked
    assert len(_source_facts(world)) == 2
    assert len(world.changes(RecordKind.PROJECT_SOURCE_LINK, source.work_source_id)) == 2


def test_an_unlink_from_a_moved_revision_is_refused(world: _World) -> None:
    repository = world.repository("winter")
    assert world.default is not None
    link = world.authoring().link_repository(world.default, repository, OP)
    world.authoring().unlink_repository(link, OP)
    with pytest.raises(ConfigRevisionConflict):
        world.links().record_repository_link(
            link,
            from_revision=1,
            change=world.changes(RecordKind.PROJECT_REPOSITORY_LINK, repository.repository_id)[-1],
        )


def test_one_source_and_one_repository_may_be_linked_by_two_projects(world: _World) -> None:
    source, repository = world.source("gh-shared"), world.repository("shared")
    assert world.default is not None
    second = world.authoring().create("winter", "Winter", OP)
    for project in (world.default, second):
        world.authoring().link_source(project, source, OP)
        world.authoring().link_repository(project, repository, OP)
    assert world.links().projects_linking_source(source.work_source_id) == sorted(
        [world.default.project_id, second.project_id]
    )
    assert [r.repository_id for r in world.links().repositories_of(second.project_id)] == [repository.repository_id]
    assert [r.repository_id for r in world.links().repositories_of(world.default.project_id)] == [
        repository.repository_id
    ]


def test_only_standing_links_count_as_linking_a_source(world: _World) -> None:
    source = world.source("gh-shared")
    assert world.default is not None
    second = world.authoring().create("winter", "Winter", OP)
    world.authoring().link_source(world.default, source, OP)
    link = world.authoring().link_source(second, source, OP)
    world.authoring().unlink_source(link, OP)
    assert world.links().projects_linking_source(source.work_source_id) == [world.default.project_id]
    assert world.links().projects_linking_source(WorkSourceId("wsrc_never")) == []


def test_linked_at_reads_the_newest_fact_set_by_then_and_the_hub_source_is_always_linked(world: _World) -> None:
    source = world.source("gh-winter")
    assert world.default is not None
    project = world.default.project_id
    world.clock.advance(timedelta(hours=1))
    link = world.authoring().link_source(world.default, source, OP)
    world.clock.advance(timedelta(hours=1))
    world.authoring().unlink_source(link, OP)
    assert not world.links().linked_at(project, source.work_source_id, _T0)
    assert world.links().linked_at(project, source.work_source_id, _T0 + timedelta(hours=1, minutes=30))
    assert not world.links().linked_at(project, source.work_source_id, _T0 + timedelta(hours=3))
    assert world.links().linked_at(project, "hub", _T0)


def test_a_retired_source_may_be_linked(world: _World) -> None:
    source = world.source("gh-old")
    retired = world.config().retire_work_source(source, OP)
    assert world.default is not None
    link = world.authoring().link_source(world.default, retired, OP)
    assert world.links().sources_of(world.default.project_id) == [link]


def test_the_links_of_one_tenant_are_invisible_to_another(world: _World) -> None:
    source = world.source("gh-winter")
    assert world.default is not None
    world.authoring().link_source(world.default, source, OP)
    other = add_tenant(world.engine, "other")
    assert world.links(other).projects_linking_source(source.work_source_id) == []
    assert world.links(other).source_link(world.default.project_id, source.work_source_id) is None


def test_the_harness_seeds_and_links_a_project_through_authoring(world: _World) -> None:
    source = world.source("gh-winter")
    repository = world.repository("winter")
    project = seed_project(world.engine, "winter")
    link_project(world.engine, project=project, source="gh-winter", repository="winter")
    assert [s.work_source_id for s in world.links().sources_of(project)] == [source.work_source_id]
    assert [r.repository_id for r in world.links().repositories_of(project)] == [repository.repository_id]
    assert len(world.changes(RecordKind.PROJECT, project)) == 1


def test_a_project_change_row_carries_its_project_id_column(world: _World) -> None:
    created = world.authoring().create("winter", "Winter", OP)
    with world.engine.connect() as conn:
        stored = conn.execute(
            world.conns()
            .select(config_changes, config_changes.c.project_id)
            .where(config_changes.c.record_kind == RecordKind.PROJECT.value)
            .where(config_changes.c.record_key == created.project_id)
        ).scalar_one()
    assert stored == created.project_id
