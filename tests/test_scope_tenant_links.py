"""Scopes keyed by a surrogate id, named per tenant (component tier, over a store migrated to head holding two
tenants).

Two tenants each hold a scope of one slug, and each attaches a routine, a routine link, a routine run, a finding and
a finding set to it through the adapters: every row links its own tenant's ``scope_id``, and each tenant's reads see
only its own. Within one tenant the slug stays unique. Links follow ``scope_id``, not the slug: a scope whose slug
changes in place keeps every routine, link and run it carries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import default_project, default_tenant, hub_scoped, migrated_hub_at, scoped_connections

_PROJECT = "proj_test"

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
_SECOND = TenantId("ten_01KSCOPESURROGATESECOND00")
_CTX = ChangeContext(actor="usr_1", door=Door.CLI)
_SLUG = "blizzard"


@dataclass(frozen=True)
class _Tenant:
    tenant: TenantId
    conns: ScopedConnections
    project: str
    scope: Scope


@dataclass(frozen=True)
class _Store:
    engine: Engine
    first: _Tenant
    second: _Tenant

    def both(self) -> Iterator[_Tenant]:
        yield self.first
        yield self.second


def _filler(column: sa.Column[Any], label: str) -> object:
    if isinstance(column.type, UtcDateTime):
        return _AT
    if isinstance(column.type, sa.Integer):
        return 1
    return f"{column.name}-{label}"


def _seed(conns: ScopedConnections, table: sa.Table, label: str, **key: object) -> None:
    """One ``table`` row holding ``key`` and a filler in every other required column, written through the seam
    confined to ``conns``' tenant."""
    required = (
        c
        for c in table.c
        if c.name != "tenant_id" and not c.nullable and c.default is None and c.server_default is None
    )
    row = {**{c.name: _filler(c, label) for c in required if c.autoincrement is not True}, **key}
    with conns.write("seed") as conn:
        conn.execute(conns.insert(table).values(row))


def _mint_scope(conns: ScopedConnections, project: str, slug: str, description: str) -> Scope:
    record, change = Scope.new(ScopeSlug.parse(slug), description, _CTX, at=_AT, project_id=project)
    return ScopeStore(conns).ensure(record, change=change)


def _tenant(engine: Engine, tenant: TenantId, label: str) -> _Tenant:
    conns = scoped_connections(engine, tenant)
    project = default_project(engine, tenant)
    scope = _mint_scope(conns, project, _SLUG, f"the {label} hub")
    routine = Routine(
        routine_id=f"rtn_{label}",
        name="nightly",
        graph_name="alpha",
        default_scope_slug=_SLUG,
        created_at=_AT,
        project_id=project,
    )
    change = ConfigChange.of(_CTX, RecordKind.ROUTINE, routine.name, routine.revision, ChangeOp.CREATE, (), _AT)
    RoutineStore(conns).create(routine, change=change, scope_mint=None)
    _seed(conns, s.work_items, label, work_item_id=f"wi_{label}")
    RunContextStore(conns).record(
        f"wi_{label}",
        RunContext(project_id=project, routine_name="nightly", scope_slug=_SLUG, mode="full"),
    )
    FindingStore(conns).add(
        f"fin_{label}",
        project_id=project,
        routine_name="nightly",
        scope_slug=_SLUG,
        class_="stale-docstring",
        locus="src/a.py",
        summary=f"seen in {label}",
        introduced=None,
        at=_AT,
    )
    _seed(conns, s.graphs, label, graph_id=f"gr_{label}")
    _seed(conns, s.chunks, label, chunk_id=f"ch_{label}", graph_id=f"gr_{label}")
    _seed(conns, s.artifacts, label, artifact_id=f"art_{label}", chunk_id=f"ch_{label}")
    FindingSetStore(conns).create(
        f"fins_{label}",
        project_id=project,
        artifact_id=f"art_{label}",
        chunk_id=f"ch_{label}",
        scope_slug=_SLUG,
        routine_name="nightly",
        revisions={},
        measurement=None,
    )
    return _Tenant(tenant=tenant, conns=conns, project=project, scope=scope)


@pytest.fixture
def store(tmp_path: Path) -> _Store:
    """A store migrated to head, its carried-over tenant and a second one each holding scope ``blizzard`` with a
    routine, a link, a run, a finding and a finding set attached."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    first = default_tenant(engine)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(s.tenants).values(tenant_id=_SECOND, name="second", created_at=_AT, created_by="usr_test")
        )
        conn.execute(
            sa.insert(s.projects).values(
                project_id="proj_second",
                tenant_id=_SECOND,
                slug="default",
                name="Default",
                description="",
                revision=1,
                created_at=_AT,
            )
        )
    return _Store(engine=engine, first=_tenant(engine, first, "first"), second=_tenant(engine, _SECOND, "second"))


def _scope_ids(engine: Engine, table: sa.Table, column: str, tenant: TenantId) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(hub_scoped(sa.select(table.c[column]).where(table.c.tenant_id == tenant))).scalars()
        return set(rows)


def test_each_tenant_holds_its_own_scope_of_one_slug(store: _Store) -> None:
    assert store.first.scope.scope_id != store.second.scope.scope_id
    for tenant in store.both():
        assert tenant.scope.scope_id.startswith("scp_")
        stored = ScopeStore(tenant.conns).get(tenant.project, _SLUG)
        assert stored is not None
        assert stored.scope_id == tenant.scope.scope_id
        assert [scope.scope_id for scope in ScopeStore(tenant.conns).list_all()] == [tenant.scope.scope_id]


def test_a_slug_stays_unique_within_its_project(store: _Store) -> None:
    again = _mint_scope(store.first.conns, store.first.project, _SLUG, "a later description")
    assert again == ScopeStore(store.first.conns).get(store.first.project, _SLUG)
    assert again.scope_id == store.first.scope.scope_id

    duplicate = {
        "scope_id": "scp_duplicate",
        "project_id": store.first.project,
        "slug": _SLUG,
        "description": "",
        "created_at": _AT,
    }
    with pytest.raises(IntegrityError), store.first.conns.write("duplicate", expect=(IntegrityError,)) as conn:
        conn.execute(store.first.conns.insert(s.scopes).values(duplicate))


@pytest.mark.parametrize(
    ("table", "column"),
    [
        (s.routines, "default_scope_id"),
        (s.routine_scopes, "scope_id"),
        (s.work_item_runs, "scope_id"),
        (s.findings, "scope_id"),
        (s.finding_sets, "scope_id"),
    ],
    ids=lambda value: getattr(value, "name", value),
)
def test_every_child_row_links_its_own_tenants_scope(store: _Store, table: sa.Table, column: str) -> None:
    for tenant in store.both():
        assert _scope_ids(store.engine, table, column, tenant.tenant) == {tenant.scope.scope_id}


def test_each_tenants_reads_see_only_its_own_attachments(store: _Store) -> None:
    for tenant, label in ((store.first, "first"), (store.second, "second")):
        assert RoutineScopeStore(tenant.conns).list_routines(tenant.scope.scope_id) == [f"rtn_{label}"]
        assert RoutineScopeStore(tenant.conns).list_scopes(f"rtn_{label}") == [_SLUG]
        routine = RoutineStore(tenant.conns).get_by_name(tenant.project, "nightly")
        assert routine is not None
        assert (routine.routine_id, routine.default_scope_slug) == (f"rtn_{label}", _SLUG)
        assert [f.finding_id for f in FindingStore(tenant.conns).list_for(tenant.project, "nightly", _SLUG)] == [
            f"fin_{label}"
        ]
        finding_set = FindingSetStore(tenant.conns).newest_for_routine_scope(tenant.project, "nightly", _SLUG)
        assert finding_set is not None
        assert finding_set.finding_set_id == f"fins_{label}"


def test_unlinking_in_one_tenant_leaves_the_other_tenants_link(store: _Store) -> None:
    second = _mint_scope(store.first.conns, store.first.project, "web", "")
    links = RoutineScopeStore(store.first.conns)
    routine = RoutineStore(store.first.conns).get("rtn_first")
    assert routine is not None
    linked = links.link(_next(routine), "web", from_revision=routine.revision, change=_edit(_next(routine)))
    assert links.list_scopes("rtn_first") == [_SLUG, "web"]

    links.unlink(_next(linked), _SLUG, from_revision=linked.revision, change=_edit(_next(linked)))

    assert links.list_scopes("rtn_first") == ["web"]
    assert _scope_ids(store.engine, s.routine_scopes, "scope_id", store.first.tenant) == {second.scope_id}
    assert RoutineScopeStore(store.second.conns).list_scopes("rtn_second") == [_SLUG]


def _next(routine: Routine) -> Routine:
    return replace(routine, revision=routine.revision + 1)


def _edit(routine: Routine) -> ConfigChange:
    return ConfigChange.of(_CTX, RecordKind.ROUTINE, routine.name, routine.revision, ChangeOp.EDIT, (), _AT)


def test_links_follow_the_scope_id_when_its_slug_changes(store: _Store) -> None:
    with store.engine.begin() as conn:
        conn.execute(
            hub_scoped(
                sa.update(s.scopes).where(s.scopes.c.scope_id == store.first.scope.scope_id).values(slug="renamed")
            )
        )

    routine = RoutineStore(store.first.conns).get("rtn_first")
    assert routine is not None
    assert routine.default_scope_slug == "renamed"
    assert RoutineScopeStore(store.first.conns).list_scopes("rtn_first") == ["renamed"]
    assert RoutineScopeStore(store.first.conns).list_routines(store.first.scope.scope_id) == ["rtn_first"]
    assert _scope_ids(store.engine, s.work_item_runs, "scope_id", store.first.tenant) == {store.first.scope.scope_id}
    assert RoutineScopeStore(store.second.conns).list_routines(store.second.scope.scope_id) == ["rtn_second"]
