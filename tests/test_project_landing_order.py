"""A project's landing order over a migrated store, driven through ``ProjectAuthoring`` — whole-order writes, single
moves, renumbering a spent gap, relinking, and the refusals, each writing position facts and never a change row
(component tier)."""

from __future__ import annotations

import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryDraft
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.model import Project
from blizzard.hub.domain.projects.order import PositionFact, RepositoryNotLinked
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_position_store import ProjectPositionStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.schema import config_changes, project_repository_positions
from blizzard.hub.store.scoped import ScopedConnections
from tests.support import OP, config_authoring, default_tenant, first_color, hub_migration_prototype, scoped_connections

pytestmark = pytest.mark.component

_T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


class _World:
    """One migrated store with a project ``winter`` linking repositories ``api``, ``lib`` and ``web``, and a repository
    ``other`` it does not link."""

    def __init__(self, tmp_path: Path) -> None:
        shutil.copy(hub_migration_prototype(), tmp_path / "hub.db")
        self.engine: Engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
        self.conns: ScopedConnections = scoped_connections(self.engine, default_tenant(self.engine))
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        self.config: ConfigAuthoring = config_authoring(self.engine, keys=keys, clock=FixedClock(_T0))
        self.config.create_secret(SecretName.parse("forge-token"), "tok", OP)
        self.links = ProjectLinkStore(self.conns)
        self.positions = ProjectPositionStore(self.conns, links=self.links)
        self.authoring = ProjectAuthoring(
            projects=ProjectStore(self.conns),
            links=self.links,
            positions=self.positions,
            pick=first_color,
            clock=FixedClock(_T0),
        )
        self.project: Project = self.authoring.create("winter", "Winter", OP)
        self.repos = {name: self._repository(name) for name in ("api", "lib", "web", "other")}
        for name in ("api", "lib", "web"):
            self.authoring.link_repository(self.project, self.repos[name], OP)

    def _repository(self, name: str) -> ConfiguredRepository:
        draft = RepositoryDraft(owner="acme", repo=name, base_branch="main", secret_name="forge-token")
        choice = ForgeChoice(api_base="https://api.github.com")
        return self.config.create_repository_naming(name, None, draft, choice, OP).record

    def order(self) -> list[tuple[str, float | None]]:
        return [(link.slug, link.position) for link in self.positions.landing_links(self.project.project_id)]

    def put(self, *names: str) -> list[tuple[str, float | None]]:
        written = self.authoring.order_repositories(self.project, [self.repos[name] for name in names], OP)
        return [(link.slug, link.position) for link in written]

    def facts(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(self.conns.select(project_repository_positions, func.count())).scalar_one()

    def change_rows(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(self.conns.select(config_changes, func.count())).scalar_one()


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_project_that_never_ordered_lands_its_repositories_by_slug(world: _World) -> None:
    assert world.order() == [("api", None), ("lib", None), ("web", None)]


def test_a_whole_order_ranks_the_named_repositories_and_writes_no_change_row(world: _World) -> None:
    changes = world.change_rows()
    assert world.put("lib", "web") == [("lib", 1.0), ("web", 2.0), ("api", None)]
    assert world.order() == [("lib", 1.0), ("web", 2.0), ("api", None)]
    assert (world.facts(), world.change_rows()) == (2, changes)


def test_a_whole_order_repeated_writes_no_fact(world: _World) -> None:
    world.put("web", "api", "lib")
    facts = world.facts()
    assert world.put("web", "api", "lib") == [("web", 1.0), ("api", 2.0), ("lib", 3.0)]
    assert world.facts() == facts


def test_a_whole_order_leaving_a_ranked_repository_out_unranks_it(world: _World) -> None:
    world.put("web", "api", "lib")
    facts = world.facts()
    assert world.put("web", "api") == [("web", 1.0), ("api", 2.0), ("lib", None)]
    assert world.facts() == facts + 1


def test_a_move_to_the_front_writes_one_fact_before_the_first(world: _World) -> None:
    world.put("web", "api")
    facts = world.facts()
    moved = world.authoring.position_repository(world.project, world.repos["lib"], OP)
    assert [(link.slug, link.position) for link in moved] == [("lib", 0.0), ("web", 1.0), ("api", 2.0)]
    assert world.facts() == facts + 1


def test_a_move_after_a_repository_takes_the_midpoint(world: _World) -> None:
    world.put("web", "api")
    world.authoring.position_repository(world.project, world.repos["lib"], OP, after=world.repos["web"])
    assert world.order() == [("web", 1.0), ("lib", 1.5), ("api", 2.0)]


def test_a_move_unranked_and_back_to_the_front(world: _World) -> None:
    world.put("web", "api", "lib")
    world.authoring.position_repository(world.project, world.repos["web"], OP, unranked=True)
    assert world.order() == [("api", 2.0), ("lib", 3.0), ("web", None)]
    world.authoring.position_repository(world.project, world.repos["web"], OP)
    assert world.order() == [("web", 1.0), ("api", 2.0), ("lib", 3.0)]


def test_a_move_to_where_it_stands_writes_nothing(world: _World) -> None:
    world.put("web", "api")
    facts = world.facts()
    world.authoring.position_repository(world.project, world.repos["web"], OP)
    world.authoring.position_repository(world.project, world.repos["api"], OP, after=world.repos["web"])
    world.authoring.position_repository(world.project, world.repos["lib"], OP, unranked=True)
    assert world.facts() == facts


def test_a_move_into_a_spent_gap_renumbers_then_takes_the_midpoint(world: _World) -> None:
    world.put("api", "lib", "web")
    pid = world.project.project_id
    spent = math.nextafter(1.0, 2.0)
    world.positions.record_positions(pid, [PositionFact(world.repos["lib"].repository_id, spent)], at=_T0, by=OP.actor)
    assert world.order() == [("api", 1.0), ("lib", spent), ("web", 3.0)]
    facts = world.facts()
    world.authoring.position_repository(world.project, world.repos["web"], OP, after=world.repos["api"])
    assert world.order() == [("api", 1.0), ("web", 1.5), ("lib", 2.0)]
    # lib renumbered to 2.0, then web at the midpoint; api already stood at 1.0.
    assert world.facts() == facts + 2


def test_relinking_a_repository_restores_its_position(world: _World) -> None:
    world.put("web", "api", "lib")
    link = world.links.repository_link(world.project.project_id, world.repos["api"].repository_id)
    assert link is not None and link.position == 2.0
    world.authoring.unlink_repository(link, OP)
    assert world.order() == [("web", 1.0), ("lib", 3.0)]
    world.authoring.link_repository(world.project, world.repos["api"], OP)
    assert world.order() == [("web", 1.0), ("api", 2.0), ("lib", 3.0)]


def test_the_repository_link_reads_carry_each_links_position(world: _World) -> None:
    world.put("lib")
    standing = world.links.repositories_of(world.project.project_id)
    by_id = {link.repository_id: link.position for link in standing}
    assert by_id == {
        world.repos["api"].repository_id: None,
        world.repos["lib"].repository_id: 1.0,
        world.repos["web"].repository_id: None,
    }


def test_ordering_an_unlinked_repository_is_refused_writing_nothing(world: _World) -> None:
    changes, facts = world.change_rows(), world.facts()
    with pytest.raises(RepositoryNotLinked) as refused:
        world.put("web", "other")
    assert (refused.value.code, refused.value.field) == ("repository_not_linked", "repositories")
    with pytest.raises(RepositoryNotLinked) as moved:
        world.authoring.position_repository(world.project, world.repos["other"], OP)
    assert (moved.value.code, moved.value.field) == ("repository_not_linked", "repository")
    with pytest.raises(RepositoryNotLinked) as anchored:
        world.authoring.position_repository(world.project, world.repos["web"], OP, after=world.repos["other"])
    assert anchored.value.field == "after_repository"
    assert (world.change_rows(), world.facts()) == (changes, facts)


def test_an_unlinked_repository_cannot_be_ordered(world: _World) -> None:
    link = world.links.repository_link(world.project.project_id, world.repos["web"].repository_id)
    assert link is not None
    world.authoring.unlink_repository(link, OP)
    with pytest.raises(RepositoryNotLinked):
        world.put("web")
