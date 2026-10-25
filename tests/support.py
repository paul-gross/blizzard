"""Shared component-test scaffolding — a fully-wired hub over a tmp sqlite store.

Builds the store-backed ``host`` composition with the work-item read seam replaced by
:class:`FakeWorkSource` (``bzh:pluggable-seams``) and a clock the test can advance
(``bzh:injected-clock``)."""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import re
import shutil
import socket
import tempfile
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlparse

import httpx2
import sqlalchemy as sa
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, make_url
from sqlalchemy import insert as sa_insert
from sqlalchemy.sql.base import Executable

from blizzard import __version__
from blizzard.auth_core import Role
from blizzard.foundation.clock import FixedClock, IClock
from blizzard.foundation.flags import Flags
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_spans import FinishedSpan
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.invitations import InvitationToken
from blizzard.hub.auth.memberships import MEMBERSHIP_ROLES
from blizzard.hub.auth.models import User
from blizzard.hub.auth.oauth.provider import IOAuthProvider
from blizzard.hub.auth.oauth.registry import OAuthProviderRegistry
from blizzard.hub.composition import (
    ForgeRegistryFactory,
    HubServices,
    LiveConfig,
    TenantServices,
    TenantStores,
    build_hub_core,
    build_services,
    default_forge_registry,
)
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH, AuthConfig, HubConfig, LoginThrottleConfig
from blizzard.hub.config_defaults import DEFAULT_ACTION_PASS_SECONDS
from blizzard.hub.delivery.action_pass import ACTION_PASS_GRACE
from blizzard.hub.delivery.action_registry import SHIPPED_ACTIONS, TEST_ACTIONS, HubActions
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    HubWorkItem,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkRef,
)
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.domain.config.secrets import IHubKeyProvider, SecretAlreadyExists, SecretName
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields, WorkSourceId
from blizzard.hub.domain.execution.auth.produces import PRODUCES_WARN
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug
from blizzard.hub.domain.graph.actions import SHIPPED_CATALOGUE, TEST_ACTION_SPECS
from blizzard.hub.domain.graph.model import Edge, Graph, Node
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.transcripts import TranscriptCaps
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.bulk_resolve import BulkProjectResolver
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerCapability, RunnerRegistration
from blizzard.hub.domain.runners.served import resolve_served
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.hub.domain.tenancy.administration import DEFAULT_PROJECT_SLUG
from blizzard.hub.egress.writer import (
    EgressBatch,
    EgressFailure,
    EgressPass,
    FilesWritten,
    IEgressWriter,
    ManifestCommitted,
    PlacedFile,
    validate_batch,
)
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.runtime import Migrations
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.store import schema
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_rows import work_ref_conn_of
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import (
    ScopedConnections,
    is_project_keyed,
    is_tenant_owned,
    statements_of,
)
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import IWorkCloser, WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.editor import IWorkEditor
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry, WorkItem, WorkSourceError
from blizzard.wire.work_source import WorkSourceDocument
from tests.config_inputs import VERSION_WINDOW, create_hub_app

_GRAPH_T0 = datetime(2026, 1, 1, tzinfo=UTC)

# The shipped catalogue with the two test actions added — what a test hands graph validation.
TEST_CATALOGUE = SHIPPED_CATALOGUE.including(TEST_ACTION_SPECS)
# The shipped actions with the two test actions added — what an in-process hub serves.
TEST_HUB_ACTIONS = HubActions.of([*SHIPPED_ACTIONS.implementations.values(), *TEST_ACTIONS])


def first_color(colors: Sequence[ProjectColor]) -> ProjectColor:
    """A deterministic project-color pick: the first candidate, in palette order."""
    return colors[0]


def hub_store_connections(engine: Engine) -> ScopedConnections:
    """The ``hub/store/internal/`` seam every adapter test wires over its
    own migrated engine, confined to the store's only tenant — one helper so every
    adapter's test file constructs it identically."""
    return scoped_connections(engine, default_tenant(engine))


def scoped_connections(engine: Engine, tenant: TenantId) -> ScopedConnections:
    """The seam confined to ``tenant`` as the test names it, read from nowhere — for a store with no
    tenant to read: one built from the bare schema, one behind the tenant revision, one gone."""
    return hub_level_connections(engine).scoped(StoreScope(tenant))


def hub_level_connections(engine: Engine) -> HubStoreConnections:
    """The unscoped seam, for the adapters over global tables (identity, exports, cross-tenant reads)."""
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))


_default_tenants: weakref.WeakKeyDictionary[Engine, TenantId] = weakref.WeakKeyDictionary()


def add_tenant(engine: Engine, name: str) -> TenantId:
    """Add a tenant named ``name`` to the store under a freshly minted id, holding its ``default``
    project, as tenant administration would, and return its id. Once a store holds two tenants, :func:`default_tenant` and :func:`stamp`
    no longer name one — a test adding one names the tenant on every raw read and write."""
    tenant = TenantId(Id.mint_at(IdPrefix.TENANT, _GRAPH_T0).value)
    project = Id.mint_at(IdPrefix.PROJECT, _GRAPH_T0).value
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.tenants).values(tenant_id=tenant, name=name, created_at=_GRAPH_T0, created_by="test")
        )
        conn.execute(
            sa.insert(schema.projects).values(
                project_id=project,
                tenant_id=tenant,
                slug="default",
                name="Default",
                description="",
                color=ProjectColor.RED,
                revision=1,
                created_at=_GRAPH_T0,
            )
        )
    return tenant


def close_tenant(engine: Engine, tenant: TenantId, *, by: str = "test", at: datetime = _GRAPH_T0) -> None:
    """Close ``tenant`` as a deletion's first step records it — one ``tenant_deletions`` row naming the tenant
    under its current name, ``by`` as its deleter and ``at`` as when it closed — so its scoped writes are refused
    from then on. Nothing else a close does in a running hub happens: a test closing a tenant a hub has cached
    refreshes what it needs, or closes through ``hub_services.tenant_teardown``."""
    tenants, deletions = schema.tenants, schema.tenant_deletions
    named = sa.select(
        tenants.c.tenant_id, tenants.c.name, sa.literal(by), sa.literal(at, type_=deletions.c.closed_at.type)
    ).where(tenants.c.tenant_id == tenant)
    with hub_level_connections(engine).write("close_tenant") as conn:
        closed = conn.execute(sa.insert(deletions).from_select(["tenant_id", "name", "deleted_by", "closed_at"], named))
    assert closed.rowcount == 1, f"close_tenant needs {tenant} among the store's tenants"


def default_tenant(engine: Engine) -> TenantId:
    """The store's only tenant — the one its first migration carried over — read once per engine.

    Raises when the store holds none or several: a test with a second tenant names the one it means."""
    cached = _default_tenants.get(engine)
    if cached is not None:
        return cached
    with engine.connect() as conn:
        ids = list(conn.execute(sa.select(schema.tenants.c.tenant_id)).scalars())
    if len(ids) != 1:
        raise AssertionError(f"default_tenant needs a store holding exactly one tenant; this one holds {len(ids)}")
    tenant = TenantId(ids[0])
    _default_tenants[engine] = tenant
    return tenant


def stamp(table: sa.Table, /, **values: Any) -> dict[str, Any]:
    """``values`` for a raw test insert into ``table``, carrying the store's tenant when the table is
    tenant-owned and the caller named none, and the row's tenant's project when the table is
    project-keyed and the caller named none — the keys a production write supplies: the scoped insert
    stamps the tenant, and the writer names the project.

    The tenant is a scalar subquery over ``tenants``, so a raw write needs no engine in hand; it names
    the store's only tenant, the same one ``default_tenant`` reads, and ``NULL`` when the store holds
    none or several — never one picked from many: a test with a second tenant names the one it means.
    The project is :func:`only_project_subquery` of the row's tenant, ``NULL`` unless it holds exactly one."""
    if is_tenant_owned(table) and "tenant_id" not in values:
        values["tenant_id"] = only_tenant_subquery()
    if is_project_keyed(table) and "project_id" not in values:
        tenant = values.get("tenant_id")
        values["project_id"] = only_project_subquery(tenant if isinstance(tenant, str) else None)
    return _with_scope_id(table, values)


#: The column each scope-linked table names its scope's slug by in a raw test seed, and whether the
#: table keeps that slug beside ``scope_id`` as its own label.
_SCOPE_SLUG_COLUMNS: dict[str, tuple[str, str, bool]] = {
    "scope_lifecycle_facts": ("slug", "scope_id", False),
    "routines": ("default_scope_slug", "default_scope_id", False),
    "routine_scopes": ("scope_slug", "scope_id", False),
    "work_item_runs": ("scope_slug", "scope_id", False),
    "findings": ("scope_slug", "scope_id", True),
    "finding_sets": ("scope_slug", "scope_id", True),
}


def _with_scope_id(table: sa.Table, values: dict[str, Any]) -> dict[str, Any]:
    """A raw seed's scope surrogate, as the scope adapters write it: a ``scopes`` row naming no
    ``scope_id`` gets a fresh ``scp_`` id; a row naming its scope by slug gets that slug's
    ``scope_id`` within the row's tenant."""
    if table.name == "scopes" and "scope_id" not in values:
        at = values.get("created_at")
        values["scope_id"] = Id.mint_at(IdPrefix.SCOPE, at if isinstance(at, datetime) else datetime.now(UTC)).value
        return values
    linked = _SCOPE_SLUG_COLUMNS.get(table.name)
    if linked is None:
        return values
    slug_column, id_column, keeps_slug = linked
    if id_column in values or slug_column not in values:
        return values
    slug = values[slug_column] if keeps_slug else values.pop(slug_column)
    tenant = values["tenant_id"]
    named = isinstance(tenant, str)
    project = values.get("project_id")
    # Raw SQL, which the tenant guard does not walk: the row's tenant may itself be the only-tenant
    # subquery, which the guard does not read as naming a tenant. The slug is looked up within the
    # row's project — the one it names, else its tenant's only one.
    tenant_sql = ":seed_scope_tenant" if named else _ONLY_TENANT_SQL
    project_sql = ":seed_scope_project" if isinstance(project, str) else _only_project_sql(tenant_sql)
    lookup = sa.text(
        f"SELECT scope_id FROM scopes WHERE slug = :seed_scope_slug AND tenant_id = ({tenant_sql})"
        f" AND project_id = ({project_sql})"
    )
    lookup = lookup.bindparams(
        seed_scope_slug=slug,
        **({"seed_scope_tenant": tenant} if named else {}),
        **({"seed_scope_project": project} if isinstance(project, str) else {}),
    )
    values[id_column] = lookup.columns(sa.column("scope_id", sa.String)).scalar_subquery()
    return values


_ONLY_TENANT_SQL = "SELECT tenant_id FROM tenants WHERE (SELECT count(*) FROM tenants) = 1"


def _only_project_sql(tenant_sql: str) -> str:
    """The project of the tenant ``tenant_sql`` selects, ``NULL`` unless that tenant holds exactly one."""
    return f"SELECT max(project_id) FROM projects WHERE tenant_id = ({tenant_sql}) HAVING count(*) = 1"


def only_project_subquery(tenant: str | None = None) -> sa.ScalarSelect[Any]:
    """``tenant``'s project as a scalar subquery — the store's only tenant's when ``tenant`` is ``None`` —
    ``NULL`` unless that tenant holds exactly one project.

    Raw SQL, which the tenant guard does not walk: the store's only tenant is itself a subquery, which
    the guard does not read as naming a tenant."""
    lookup = sa.text(_only_project_sql(":only_project_tenant" if tenant is not None else _ONLY_TENANT_SQL))
    if tenant is not None:
        lookup = lookup.bindparams(only_project_tenant=tenant)
    return lookup.columns(sa.column("project_id", sa.String)).scalar_subquery()


def default_project(engine: Engine, tenant: TenantId | None = None) -> str:
    """The id of ``tenant``'s only project — the store's only tenant's when ``tenant`` is ``None``.

    Raises when the tenant holds none or several: a test with a second project names the one it means."""
    tenant = tenant if tenant is not None else default_tenant(engine)
    projects = schema.projects
    with engine.connect() as conn:
        ids = list(conn.execute(sa.select(projects.c.project_id).where(projects.c.tenant_id == tenant)).scalars())
    if len(ids) != 1:
        raise AssertionError(f"default_project needs a tenant holding exactly one project; {tenant} holds {len(ids)}")
    return ids[0]


def project_record(hub: HubHarness, project_id: str) -> Project:
    """The stored ``Project`` ``project_id`` names — the loaded object a domain write takes in place of the id."""
    project = hub.services.projects.get(ProjectId(project_id))
    assert project is not None, f"project_record needs {project_id} stored"
    return project


_SEEDER = ChangeContext(actor="test", door=Door.API)


def _project_authoring(engine: Engine, tenant: TenantId) -> tuple[ProjectAuthoring, ProjectStore]:
    conns = scoped_connections(engine, tenant)
    projects = ProjectStore(conns)
    return ProjectAuthoring(
        projects=projects, links=ProjectLinkStore(conns), pick=first_color, clock=FixedClock(_GRAPH_T0)
    ), projects


def seed_project(engine: Engine, slug: str, *, tenant: TenantId | None = None) -> ProjectId:
    """Create a project named ``slug`` in ``tenant`` — the store's only tenant when ``None`` — through project
    authoring, its create change row included, and return its id. Once the tenant holds two projects, a write that
    names none is refused: the test names the project on every write it makes from then on."""
    authoring, _ = _project_authoring(engine, tenant if tenant is not None else default_tenant(engine))
    return authoring.create(slug, slug, _SEEDER).project_id


def link_project(
    engine: Engine,
    *,
    project: str | None = None,
    source: str | None = None,
    repository: str | None = None,
    tenant: TenantId | None = None,
) -> None:
    """Link ``project`` — ``tenant``'s only project when ``None`` — to the work source ``source`` and/or the
    repository ``repository``, through project authoring: a first link, or a relink of one it unlinked. ``tenant``
    is the store's only tenant when ``None``. The named records must be stored in that tenant."""
    tenant = tenant if tenant is not None else default_tenant(engine)
    authoring, projects = _project_authoring(engine, tenant)
    record = projects.get(ProjectId(project if project is not None else default_project(engine, tenant)))
    assert record is not None, f"link_project needs {project} among {tenant}'s projects"
    conns = scoped_connections(engine, tenant)
    if source is not None:
        work_source = WorkSourceRecordStore(conns).resolve(source)
        assert work_source is not None, f"link_project needs the work source {source} stored in {tenant}"
        authoring.link_source(record, work_source, _SEEDER)
    if repository is not None:
        stored = RepositoryRecordStore(conns).resolve(repository)
        assert stored is not None, f"link_project needs the repository {repository} stored in {tenant}"
        authoring.link_repository(record, stored, _SEEDER)


def seed_scope(conns: ScopedConnections, slug: str, *, at: datetime, project_id: str | None = None) -> str:
    """Store a scope named ``slug`` in ``project_id`` — ``conns``' tenant's only project when ``None`` — through
    its adapter unless the project stores one — the parent a run, link or finding naming ``slug`` needs — and
    return its ``scope_id``."""
    project = project_id if project_id is not None else _only_project_of(conns)
    record, change = Scope.new(ScopeSlug.parse(slug), "", OP, project_id=project, at=at)
    return ScopeStore(conns).ensure(record, change=change).scope_id


def _only_project_of(conns: ScopedConnections) -> str:
    """The id of ``conns``' tenant's only project; raises when it holds none or several."""
    projects = schema.projects
    with conns.read("only_project_of") as conn:
        ids: list[str] = list(conn.execute(conns.select(projects, projects.c.project_id)).scalars())
    if len(ids) != 1:
        raise AssertionError(f"the tenant must hold exactly one project; it holds {len(ids)}")
    return ids[0]


def only_tenant_subquery() -> sa.ScalarSelect[Any]:
    """The store's only tenant as a scalar subquery, ``NULL`` unless ``tenants`` holds exactly one row."""
    tenants = schema.tenants
    holds_one = sa.select(sa.func.count()).select_from(tenants).correlate(None).scalar_subquery() == 1
    return sa.select(tenants.c.tenant_id).where(holds_one).scalar_subquery()


def hub_scoped[E: Executable](statement: E) -> E:
    """``statement`` marked deliberately cross-tenant: a test's raw read or write of the store as a whole,
    exempt from the tenant statement guard."""
    return statement.execution_options(**{HUB_SCOPED_OPTION: True})


def hub_scoped_engine(engine: Engine) -> Engine:
    """``engine`` with every statement marked cross-tenant: a migration test's store, seeded and probed at
    revisions older than the live schema, where no tenant scope applies."""
    return engine.execution_options(**{HUB_SCOPED_OPTION: True})


OP = ChangeContext(actor="op", door=Door.API)

#: The clock a test's hub migration hands the revisions that stamp a time (``migration_clock``).
MIGRATION_CLOCK = FixedClock(datetime(2026, 7, 12, tzinfo=UTC))


def config_authoring(engine: Engine, *, keys: IHubKeyProvider, clock: IClock) -> ConfigAuthoring:
    """The one configured-record writer over ``engine``, sealing under ``keys``."""
    store = hub_store_connections(engine)
    return ConfigAuthoring(
        work_sources=WorkSourceRecordStore(store),
        repositories=RepositoryRecordStore(store),
        secrets=SecretStore(store),
        cipher=secret_cipher(keys),
        apply_writer=ConfigApplyStore(store),
        import_writer=ConfigImportStore(store),
        clock=clock,
    )


def chunk_stores(engine: Engine, clock: IClock) -> ChunkStores:
    """All chunk-seam adapters over one engine/clock, bundled the same shape
    ``src/blizzard/hub/composition.py`` wires in production (:func:`build_chunk_stores`) — the
    store-level test's own single-object fixture-setup convenience a per-seam physical
    split would otherwise take from it. A test calls ``stores.<seam>.<method>(...)`` in
    place of the old single ``ChunkStore``'s bare method call."""
    store = hub_store_connections(engine)
    return build_chunk_stores(store, clock, registry=RunnerRegistryStore(store))


def make_graph(
    graph_id: str,
    name: str,
    *,
    entry_node_id: str = "nd_entry",
    nodes: list[Node] | None = None,
    edges: list[Edge] | None = None,
    created_at: datetime = _GRAPH_T0,
) -> Graph:
    """A minimal :class:`Graph` — defaults to no nodes/edges, a fixed ``created_at``."""
    return Graph(
        graph_id=graph_id,
        name=name,
        entry_node_id=entry_node_id,
        nodes=nodes if nodes is not None else [],
        edges=edges if edges is not None else [],
        created_at=created_at,
    )


class InMemoryTraceExporter:
    """An in-process :class:`ITraceExporter` — records each batch it accepts.

    ``fail`` set makes every export refuse its batch; ``raises`` makes it raise instead.
    ``attempts`` counts every call, accepted or not."""

    def __init__(self) -> None:
        self.batches: list[tuple[FinishedSpan, ...]] = []
        self.attempts = 0
        self.fail = False
        self.raises = False

    def export(self, spans: Sequence[FinishedSpan]) -> bool:
        self.attempts += 1
        if self.raises:
            raise RuntimeError("in-memory trace exporter told to raise")
        if self.fail:
            return False
        self.batches.append(tuple(spans))
        return True

    @property
    def spans(self) -> list[FinishedSpan]:
        return [span for batch in self.batches for span in batch]


class FakeWorkSource:
    """An in-process :class:`IWorkSource` — canned title + body + comments per pointer ref.

    Keyed on ``pointer.ref`` rather than a URL; ``by_ref``/``fail_refs`` override or fail
    specific refs to exercise per-pointer forge-failure degradation."""

    def __init__(
        self,
        *,
        name: str = "default",
        repo: str = "acme/widget",
        title: str = "issue title",
        body: str = "issue body",
        comments: list[str] | None = None,
        by_ref: dict[str, WorkItem] | None = None,
        fail_refs: set[str] | None = None,
    ) -> None:
        self.name = name
        self.repo = repo
        self.title = title
        self.body = body
        self.comments = comments or []
        self.by_ref = by_ref or {}
        self.fail_refs = fail_refs or set()
        self.fetched: list[str] = []

    def parse(self, token: str) -> WorkRef | None:
        """``{name}:{ref}`` or ``{name}#{ref}``; ``None`` otherwise — no URL grammar, and
        any non-empty ``ref`` shape is accepted."""
        for sep_char in (":", "#"):
            prefix, sep, ref = token.partition(sep_char)
            if sep and prefix == self.name and ref:
                return WorkRef(source=self.name, ref=ref)
        return None

    def fetch(self, pointer: WorkRef) -> WorkItem:
        self.fetched.append(pointer.ref)
        if pointer.ref in self.fail_refs:
            raise WorkSourceError(f"forge unreachable for {pointer.ref}")
        if pointer.ref in self.by_ref:
            return self.by_ref[pointer.ref]
        return WorkItem(body=self.body, title=self.title, comments=list(self.comments))

    def label(self, pointer: WorkRef) -> str | None:
        return f"{self.name}#{pointer.ref}"

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        return f"http://forge.local/{self.repo}/issues/{pointer.ref}"

    def forge_reference(self, pointer: WorkRef) -> str | None:
        return f"{self.repo}#{pointer.ref}"


def _conforms_fake_work_source(x: FakeWorkSource) -> IWorkSource:
    return x


class WorkSourceRegistry:
    """A dict-backed work-source registry — the test double for the store-backed one.

    ``annotators``/``closers``/``editors`` are each a subset of ``sources``, so an absent key has no
    write half; ``label_clearers`` holds every forge source's annotator, reached only to clear labels. ``slugs`` maps
    a key to the slug it displays under, the key itself when absent."""

    def __init__(
        self,
        sources: Mapping[str, IWorkSource] | None = None,
        annotators: Mapping[str, IWorkAnnotator] | None = None,
        closers: Mapping[str, IWorkCloser] | None = None,
        editors: Mapping[str, IWorkEditor] | None = None,
        label_clearers: Mapping[str, IWorkAnnotator] | None = None,
        slugs: Mapping[str, str] | None = None,
    ) -> None:
        self._slugs = dict(slugs or {})
        self._sources = dict(sources or {})
        self._annotators = dict(annotators or {})
        self._closers = dict(closers or {})
        self._editors = dict(editors or {})
        self._label_clearers = {**dict(label_clearers or {}), **self._annotators}

    def get(self, source: str) -> IWorkSource | None:
        return self._sources.get(source)

    def names(self) -> list[str]:
        return [self.source_label(key) for key in self._sources]

    def source_label(self, source: str) -> str:
        return self._slugs.get(source, source)

    def label(self, pointer: WorkRef) -> str:
        source = self._sources.get(pointer.source)
        rendered = source.label(pointer) if source is not None else None
        return rendered if rendered is not None else f"{self.source_label(pointer.source)}#{pointer.ref}"

    def annotator(self, source: str) -> IWorkAnnotator | None:
        return self._annotators.get(source)

    def annotating_ids(self) -> list[str]:
        return list(self._annotators.keys())

    def label_clearer(self, source: str) -> IWorkAnnotator | None:
        return self._label_clearers.get(source)

    def closer(self, source: str) -> IWorkCloser | None:
        return self._closers.get(source)

    def editor(self, source: str) -> IWorkEditor | None:
        return self._editors.get(source)

    def resolve(self, token: str) -> WorkRef | None:
        for source in self._sources.values():
            pointer = source.parse(token)
            if pointer is not None:
                return pointer
        return None


def _conforms_work_source_registry(x: WorkSourceRegistry) -> IWorkSourceRegistry:
    return x


class FakeAnnotator:
    """An in-process :class:`IWorkAnnotator` — an in-memory ``{ref: {markers}}`` map,
    plus call logs a test asserts against. ``fail_refs`` raises
    :class:`WorkAnnotateError` for a ref's own :meth:`set_status`/:meth:`clear_status`,
    mirroring :class:`FakeWorkSource`'s own per-pointer failure knob."""

    def __init__(
        self,
        *,
        initial: dict[WorkRef, set[WorkStatusMarker]] | None = None,
        fail_refs: set[str] | None = None,
    ) -> None:
        self._marks: dict[WorkRef, set[WorkStatusMarker]] = {
            ref: set(markers) for ref, markers in (initial or {}).items()
        }
        self.fail_refs = fail_refs or set()
        self.set_calls: list[tuple[WorkRef, WorkStatusMarker]] = []
        self.clear_calls: list[WorkRef] = []
        self.read_calls = 0

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        if pointer.ref in self.fail_refs:
            raise WorkAnnotateError(f"boom setting {marker.value} on {pointer.ref}")
        self.set_calls.append((pointer, marker))
        self._marks[pointer] = {marker}

    def clear_status(self, pointer: WorkRef) -> None:
        if pointer.ref in self.fail_refs:
            raise WorkAnnotateError(f"boom clearing {pointer.ref}")
        self.clear_calls.append(pointer)
        self._marks.pop(pointer, None)

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        self.read_calls += 1
        return {ref: frozenset(markers) for ref, markers in self._marks.items() if markers}


def _conforms_fake_annotator(x: FakeAnnotator) -> IWorkAnnotator:
    return x


class FakeCloser:
    """An in-process :class:`IWorkCloser` — an in-memory ``{ref: outcome}`` map plus
    a call log a test asserts against. ``gone_refs`` raises
    :class:`WorkItemGoneError`; ``fail_refs`` raises a bare :class:`WorkCloseError`."""

    def __init__(self, *, gone_refs: set[str] | None = None, fail_refs: set[str] | None = None) -> None:
        self.gone_refs = gone_refs or set()
        self.fail_refs = fail_refs or set()
        self.closed: list[WorkRef] = []
        self.traces: list[DeliveryTrace | None] = []

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        if pointer.ref in self.gone_refs:
            raise WorkItemGoneError(f"{pointer.ref} no longer exists")
        if pointer.ref in self.fail_refs:
            raise WorkCloseError(f"boom closing {pointer.ref}")
        self.closed.append(pointer)
        self.traces.append(trace)


def _conforms_fake_closer(x: FakeCloser) -> IWorkCloser:
    return x


class _OmitTitle:
    """The sentinel a test uses to make :func:`github_double` omit ``title`` from the payload."""

    def __repr__(self) -> str:
        return "OMIT_TITLE"


OMIT_TITLE = _OmitTitle()
"""Sentinel — a forge payload with no ``title`` key at all (real GitHub never sends this)."""


def github_double(
    *,
    conflict_branches: set[str] | None = None,
    issues: dict[str, dict] | None = None,
    pull_numbers: set[int] | None = None,
) -> TestClient:
    """A tiny GitHub-shaped forge double for the real HTTP adapters.

    Exercises the adapter HTTP shaping against a minimal GitHub-REST-v3 surface — issue
    read + comments, PR create + merge, and label routes — without coupling this repo to
    ``blizzard-mock`` as a dev dependency. Wrapped in a ``TestClient``."""
    from fastapi.responses import JSONResponse

    conflict = conflict_branches or set()
    issue_store = issues or {}
    app = FastAPI()
    state: dict[str, object] = {
        "next_pull": 1,
        "pulls": {},
        "repo_labels": {},
        "issue_labels": {},
        "pr_numbers": set(pull_numbers or set()),
        "issues": issue_store,
    }

    @app.get("/repos/{owner}/{repo}/issues/{number}")
    def get_issue(owner: str, repo: str, number: int) -> dict:
        key = f"{owner}/{repo}#{number}"
        data = issue_store.get(key, {"body": f"issue {number}", "comments": []})
        payload: dict[str, object] = {"number": number, "body": data["body"]}
        # A double laxer than the forge it stands for would hide bugs, so ``title`` is
        # present by default and a test opts into the degenerate shapes explicitly.
        title = data.get("title", f"issue {number}")
        if title is not OMIT_TITLE:
            payload["title"] = title
        return payload

    @app.patch("/repos/{owner}/{repo}/issues/{number}")
    def update_issue_state(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        # A number the double's fixed issue store doesn't know about is the "gone"
        # case a closer must surface distinctly from a generic failure.
        issue_state: dict[str, dict] = state.setdefault("issue_state", {})  # type: ignore[assignment]
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store and key not in issue_state:
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        issue_state[key] = {"state": body["state"], "state_reason": body.get("state_reason")}
        return JSONResponse(status_code=200, content={"number": number, **issue_state[key]})

    @app.get("/repos/{owner}/{repo}/issues/{number}/comments")
    def get_comments(
        request: Request, owner: str, repo: str, number: int, page: int = 1, per_page: int = 30
    ) -> JSONResponse:
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store and key not in state.setdefault("issue_state", {}):  # type: ignore[call-overload]
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        comments = issue_store.get(key, {}).get("comments", [])
        chunk = comments[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(comments):
            headers["Link"] = f'<{request.url.include_query_params(page=page + 1, per_page=per_page)}>; rel="next"'
        return JSONResponse(content=[{"body": c} for c in chunk], headers=headers)

    @app.post("/repos/{owner}/{repo}/issues/{number}/comments")
    def post_comment(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        key = f"{owner}/{repo}#{number}"
        if key not in issue_store:
            return JSONResponse(status_code=404, content={"message": "Not Found"})
        issue_store[key].setdefault("comments", []).append(body["body"])
        return JSONResponse(status_code=201, content={"body": body["body"]})

    @app.post("/repos/{owner}/{repo}/pulls")
    def create_pull(owner: str, repo: str, body: dict) -> JSONResponse:
        pulls = state["pulls"]  # type: ignore[index]
        if any(p["state"] == "open" and p["head"] == body["head"] for p in pulls.values()):  # type: ignore[union-attr]
            # GitHub 422s a second PR for the same head — the redelivery reuse path.
            return JSONResponse(status_code=422, content={"message": "A pull request already exists"})
        number = int(state["next_pull"])  # type: ignore[arg-type]
        state["next_pull"] = number + 1
        state["pulls"][number] = {  # type: ignore[index]
            "head": body["head"],
            "base": body["base"],
            "merged": False,
            "state": "open",
            "merge_commit_sha": None,
        }
        return JSONResponse(
            status_code=201,
            content={
                "number": number,
                "html_url": f"http://forge/{owner}/{repo}/pull/{number}",
                "head": {"ref": body["head"]},
            },
        )

    @app.get("/repos/{owner}/{repo}/pulls")
    def list_pulls(owner: str, repo: str, state_: str = "open") -> list[dict]:
        pulls = state["pulls"]  # type: ignore[index]
        return [
            {
                "number": n,
                "head": {"ref": p["head"]},
                "state": p["state"],
                "html_url": f"http://forge/{owner}/{repo}/pull/{n}",
            }
            for n, p in pulls.items()  # type: ignore[union-attr]
            if p["state"] == state_
        ]

    @app.get("/repos/{owner}/{repo}/pulls/{number}")
    def get_pull(owner: str, repo: str, number: int) -> dict:
        p = state["pulls"].get(number, {})  # type: ignore[union-attr]
        return {
            "number": number,
            "head": {"ref": p.get("head")},
            "merged": p.get("merged", False),
            "state": p.get("state", "open"),
            "merge_commit_sha": p.get("merge_commit_sha"),
        }

    @app.put("/repos/{owner}/{repo}/pulls/{number}/merge")
    def merge_pull(owner: str, repo: str, number: int, body: dict) -> JSONResponse:
        pull = state["pulls"].get(number, {})  # type: ignore[union-attr]
        if pull.get("head") in conflict:
            return JSONResponse(status_code=409, content={"message": "not mergeable"})
        merge_sha = f"merged-{body.get('sha')}"
        pull.update({"merged": True, "state": "closed", "merge_commit_sha": merge_sha})
        return JSONResponse(status_code=200, content={"sha": merge_sha, "merged": True, "message": "ok"})

    def _forbidden_if_armed() -> JSONResponse | None:
        """The ``forbidden`` lever a test arms via ``client.forge_state["forbidden"]
        = True`` — an insufficient-scope 403 (the rate-limit 403 is not modelled)."""
        if state.get("forbidden"):
            return JSONResponse(status_code=403, content={"message": "Resource not accessible by integration"})
        return None

    @app.post("/repos/{owner}/{repo}/labels")
    def create_repo_label(owner: str, repo: str, body: dict) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        repo_labels = state["repo_labels"].setdefault(f"{owner}/{repo}", set())  # type: ignore[union-attr]
        name = body["name"]
        if name in repo_labels:
            return JSONResponse(status_code=422, content={"message": "already_exists"})
        repo_labels.add(name)
        state.setdefault("repo_label_colors", {})[name] = body.get("color")  # type: ignore[union-attr]
        return JSONResponse(status_code=201, content={"name": name})

    @app.post("/repos/{owner}/{repo}/issues/{number}/labels")
    def add_issue_labels(owner: str, repo: str, number: int, body: list[str]) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        # `label_add_forbidden` fails only this route; the broad `forbidden` lever can't
        # isolate an add failure since it trips the bootstrap POST first.
        if state.get("label_add_forbidden"):
            return JSONResponse(status_code=403, content={"message": "Resource not accessible by integration"})
        key = f"{owner}/{repo}#{number}"
        issue_labels = state["issue_labels"].setdefault(key, set())  # type: ignore[union-attr]
        issue_labels.update(body)
        return JSONResponse(status_code=200, content=[{"name": name} for name in sorted(issue_labels)])

    @app.delete("/repos/{owner}/{repo}/issues/{number}/labels/{name}")
    def remove_issue_label(owner: str, repo: str, number: int, name: str) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        key = f"{owner}/{repo}#{number}"
        issue_labels = state["issue_labels"].setdefault(key, set())  # type: ignore[union-attr]
        if name not in issue_labels:
            return JSONResponse(status_code=404, content={"message": "Label does not exist"})
        issue_labels.discard(name)
        return JSONResponse(status_code=200, content=[{"name": n} for n in sorted(issue_labels)])

    @app.get("/repos/{owner}/{repo}/issues")
    def list_issues(
        owner: str, repo: str, request: Request, labels: str | None = None, per_page: int = 30, page: int = 1
    ) -> JSONResponse:
        if (forbidden := _forbidden_if_armed()) is not None:
            return forbidden
        repo_key = f"{owner}/{repo}"
        wanted = set(labels.split(",")) if labels else None
        issue_labels_map: dict[str, set[str]] = state["issue_labels"]  # type: ignore[assignment]
        pr_numbers: set[int] = state["pr_numbers"]  # type: ignore[assignment]
        numbers = sorted(
            int(key.rpartition("#")[2])
            for key, names in issue_labels_map.items()
            if key.startswith(f"{repo_key}#") and (wanted is None or wanted <= names)
        )
        start = (page - 1) * per_page
        page_numbers = numbers[start : start + per_page]
        items: list[dict] = []
        for n in page_numbers:
            item: dict[str, object] = {
                "number": n,
                "labels": [{"name": name} for name in sorted(issue_labels_map.get(f"{repo_key}#{n}", set()))],
            }
            if n in pr_numbers:
                item["pull_request"] = {"url": f"http://forge/{repo_key}/pulls/{n}"}
            items.append(item)
        headers = {}
        if start + per_page < len(numbers):
            next_url = str(request.url.include_query_params(page=page + 1))
            headers["Link"] = f'<{next_url}>; rel="next"'
        return JSONResponse(status_code=200, content=items, headers=headers)

    client = TestClient(app)
    client.forge_state = state  # type: ignore[attr-defined] # tests flip PR fate (e.g. close-without-merge)
    return client


def forge_state(double: TestClient) -> dict[str, object]:
    """Typed accessor for a :func:`github_double`'s mutable state dict — a test
    seeds/reads ``issue_labels``/``repo_labels``/``pr_numbers``/``forbidden``/
    ``label_add_forbidden`` directly."""
    return double.forge_state  # type: ignore[attr-defined]


def runner_token(runner_id: str) -> str:
    """The bearer token a test runner holds — derived from its id, so a second hub built over the
    same store, or a second client, recomputes it instead of sharing state."""
    return f"test-token-{runner_id}"


#: What a seeded runner declares unless a test names its own: the claude default a live runner reports.
SEEDED_CAPABILITIES = (RunnerCapability("claude", default=True),)


def seed_runner(
    target: HubHarness | TenantServices,
    runner_id: str,
    *,
    name: str | None = None,
    workspace_id: str = "w1",
    token: str | None = None,
    capabilities: tuple[RunnerCapability, ...] = SEEDED_CAPABILITIES,
    register: bool = True,
    projects: tuple[str, ...] | None = None,
) -> str:
    """Add ``runner_id`` at the hub as ``hub runner add`` does, but under a chosen id and holding
    ``token`` (default :func:`runner_token`), and — with ``register`` — record its first registration
    so it is connected; returns the plaintext token. ``register=False`` leaves it added but never
    connected. A runner the hub already holds is left as is, except that ``register`` connects one
    that never has. It serves ``projects``, each an id or slug, and the tenant's ``default`` project
    when ``None``, whatever other projects the tenant holds."""
    services = target.services if isinstance(target, HubHarness) else target
    registry = services.registry
    assert isinstance(registry, RunnerRegistryStore), "seeding writes through the hub's own registry store"
    plaintext = token or runner_token(runner_id)
    token_hash = TokenHash(plaintext).hex
    present = registry.get_runner(runner_id)
    if present is None:
        declared = (DEFAULT_PROJECT_SLUG,) if projects is None else projects
        served = resolve_served(declared, BulkProjectResolver(services.projects))
        registry.add(
            RunnerAddition(runner_id, name or runner_id, token_hash, at=services.clock.now(), by="test", served=served)
        )
    if register and (present is None or present.never_connected()):
        services.fleet.register(added_runner(services, runner_id), workspace_id, capabilities=capabilities)
    return plaintext


def added_runner(target: HubHarness | TenantServices, runner_id: str) -> RunnerRegistration:
    """The registration the hub holds for ``runner_id`` — what an edge resolves before handing a
    runner to the fleet service; fails the test when the hub never added it."""
    services = target.services if isinstance(target, HubHarness) else target
    registration = services.registry.get_runner(runner_id)
    assert registration is not None, f"runner {runner_id} has not been added"
    return registration


_FLEET_PREFIX = "/api/fleet/"
_FLEET_RUNNERS_PREFIX = "/api/fleet/runners/"


class RunnerFleetClient(TestClient):
    """The hub harness's client: a call under ``/api/fleet/`` that carries no ``Authorization`` header presents
    the bearer token (:func:`runner_token`) of the runner it names, adding that runner, never connected, when the
    hub does not hold it, and registers a never-connected claimant before a claim unless ``register_claimants=False``.
    A test pinning a tokenless refusal uses a plain ``TestClient``.

    Runner ids are unique across the hub, so a client serving a tenant other than the default one carries a
    ``runner_prefix``: its default runner is the prefixed ``default_runner_id``, and it refuses to add a runner whose
    id lacks the prefix rather than collide with another tenant's."""

    def __init__(
        self,
        app: FastAPI,
        *,
        services: TenantServices,
        default_runner_id: str = "r1",
        register_claimants: bool = True,
        runner_prefix: str = "",
    ) -> None:
        super().__init__(app)
        self.default_runner_id = f"{runner_prefix}{default_runner_id}"
        self._runner_prefix = runner_prefix
        self._services = services
        self._register_claimants = register_claimants
        self._seeding = threading.Lock()
        # Ids this client has seen present (or connected): neither condition is ever undone, so a
        # repeat call reads nothing extra and a statement-counting test sees only the route's reads.
        self._present: set[str] = set()
        self._connected: set[str] = set()

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        path = httpx2.URL(str(url)).path
        headers = httpx2.Headers(kwargs.get("headers"))
        if path.startswith(_FLEET_PREFIX) and "authorization" not in headers and "authorization" not in self.headers:
            runner_id = self._caller(str(url), path, kwargs)
            claim = method.upper() == "POST" and path == "/api/fleet/routes"
            self._seed(runner_id, kwargs.get("json"), connect=claim and self._register_claimants)
            headers["Authorization"] = f"Bearer {runner_token(runner_id)}"
            kwargs["headers"] = headers
        return super().request(method, url, *args, **kwargs)

    def _caller(self, url: str, path: str, kwargs: Mapping[str, Any]) -> str:
        body = kwargs.get("json")
        if isinstance(body, dict) and isinstance(body.get("runner_id"), str):
            return str(body["runner_id"])
        if path.startswith(_FLEET_RUNNERS_PREFIX):
            return path.removeprefix(_FLEET_RUNNERS_PREFIX).split("/", 1)[0]
        query = httpx2.URL(url).params.get("runner_id") or httpx2.QueryParams(kwargs.get("params")).get("runner_id")
        return query or self.default_runner_id

    def _seed(self, runner_id: str, body: object, *, connect: bool) -> None:
        known = self._connected if connect else self._present
        if runner_id in known:
            return
        assert runner_id.startswith(self._runner_prefix), (
            f"runner ids are unique across the hub: name {runner_id!r} with this tenant's {self._runner_prefix!r} prefix"
        )
        with self._seeding:
            workspace_id = body.get("workspace_id") if isinstance(body, dict) else None
            seed_runner(self._services, runner_id, workspace_id=str(workspace_id or "w1"), register=connect)
            self._present.add(runner_id)
            if connect:
                self._connected.add(runner_id)


@dataclass
class HubHarness:
    """A wired hub app plus the collaborators a test drives and asserts against."""

    client: TestClient
    #: The default tenant's services — the tenant every one-tenant test is served in.
    services: TenantServices
    #: The hub-level services — identity, sessions, signing, exports and sweeps.
    hub_services: HubServices
    work_sources: WorkSourceRegistry
    clock: FixedClock
    engine: Engine
    events: EventBroker = field(default_factory=EventBroker)
    #: The wired app, so a test can build a second ``TestClient`` with a different peer
    #: address — the forwarded-header trust tests need a concrete IP peer.
    app: FastAPI | None = None

    @property
    def wired_app(self) -> FastAPI:
        """The wired app, as the :class:`FastAPI` it is — ``client.app`` types it as any ASGI app."""
        assert self.app is not None
        return self.app

    def open_services(self, tenant: TenantId) -> TenantServices:
        """``tenant``'s services, opened as a request in that tenant would open them."""
        return self.hub_services.tenants.open_services(StoreScope(tenant))

    def add_tenant(self, name: str) -> TenantHarness:
        """Add a tenant named ``name`` beside the default one and hand back what a test drives it with: its
        services, and a client that names it in ``X-Blizzard-Tenant`` on every call and seeds runners as
        ``<name>-<id>``. The default tenant stays named; the hub's open-tenant set is refreshed, as tenant
        administration refreshes it."""
        assert self.app is not None
        default_tenant(self.engine)
        tenant = add_tenant(self.engine, name)
        self.hub_services.tenants.open_tenants.refresh()
        services = self.open_services(tenant)
        prefix = f"{name}-"
        client = RunnerFleetClient(self.app, services=services, runner_prefix=prefix)
        client.headers[TENANT_HEADER] = tenant
        return TenantHarness(tenant=tenant, name=name, services=services, client=client, runner_prefix=prefix)

    def promote(self, chunk_id: str) -> None:
        """Promote ``chunk_id`` through the real ``POST /api/chunks/{id}/promote`` path."""
        assert self.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202


@dataclass(frozen=True)
class TenantHarness:
    """A tenant :meth:`HubHarness.add_tenant` added: its id and name, its services, and a client acting in it."""

    tenant: TenantId
    name: str
    services: TenantServices
    #: Names the tenant on every call; a fleet call's runner is seeded in this tenant under :attr:`runner_prefix`.
    client: RunnerFleetClient
    runner_prefix: str

    def runner_id(self, local: str) -> str:
        """The hub-unique id this tenant's runner ``local`` is seeded under."""
        return f"{self.runner_prefix}{local}"


def tenant_services_of(app: FastAPI) -> TenantServices:
    """The services of a wired ``app``'s only open tenant — the name every test reads a tenant field of an
    app through; a hub-level field is read off ``app.state.services`` itself."""
    tenants = app.state.services.tenants
    tenant = tenants.open_tenants.only()
    assert tenant is not None, "tenant_services_of needs an app serving exactly one open tenant"
    return tenants.open_services(StoreScope(tenant))


def serve_default_tenant(hub: HubHarness, services: TenantServices) -> None:
    """Serve every request in the default tenant from ``services`` — a test's stand-in, usually
    ``hub.services`` with a field replaced by a counting double."""
    assert hub.app is not None
    tenants = hub.hub_services.tenants
    default = default_tenant(hub.engine)

    def open_services(scope: StoreScope) -> TenantServices:
        return services if scope.tenant == default else tenants.open_services(scope)

    hub.app.state.services = replace(hub.hub_services, tenants=replace(tenants, open_services=open_services))


_hub_prototype_lock = threading.Lock()
_hub_prototype_tmp: tempfile.TemporaryDirectory[str] | None = None
_hub_prototype_db: Path | None = None


def hub_migration_prototype() -> Path:
    """A hub store migrated to head exactly once per process; ``build_hub`` copies this
    file rather than re-running all ~89 hub revisions on every call.

    Safe to share across every ``build_hub`` call regardless of the ``HubConfig`` a test
    passes: the migration tree branches on nothing but its own code, never on a config
    field. Lives under its own per-process temp dir, cleaned up by
    :class:`tempfile.TemporaryDirectory`'s own finalizer."""
    global _hub_prototype_tmp, _hub_prototype_db
    with _hub_prototype_lock:
        if _hub_prototype_db is None:
            _hub_prototype_tmp = tempfile.TemporaryDirectory(prefix="blizzard-hub-migration-proto-")
            root = Path(_hub_prototype_tmp.name)
            db_url = f"sqlite:///{root / 'hub.db'}"
            Migrations(HubConfig(root=root, db_url=db_url), MIGRATION_CLOCK).runner.upgrade("head")
            checkpoint_sqlite(db_url)
            _hub_prototype_db = root / "hub.db"
        return _hub_prototype_db


def migrated_hub_at(db_url: str) -> None:
    """Lay a head-migrated hub store at the URL's sqlite file unless it already exists."""
    path = Path(make_url(db_url).database or "")
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(hub_migration_prototype(), path)


def checkpoint_sqlite(db_url: str) -> None:
    """Flush a sqlite file's WAL back into itself and drop the connection — a bare copy
    of the ``.db`` file is only a complete store once no ``-wal``/``-shm`` sidecar is
    load-bearing."""
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        engine.dispose()


#: The repository every hub built without an explicit ``repositories`` carries — the ``acme/widget``
#: the component tier's commits name — so a deliver node resolves them.
DEFAULT_FIXTURE_REPOSITORIES = (
    RepositoryFields(
        forge_api_url="http://forge.fixture",
        owner="acme",
        repo="widget",
        base_branch="main",
        secret_name="fixture-forge-token",
    ),
)


def seed_repositories(
    engine: Engine, repositories: Sequence[RepositoryFields], *, tmp_path: Path, link: bool = True
) -> None:
    """Write ``repositories`` and the secret they name straight into the store, as an operator's
    ``blizzard hub repo create`` would, and link each to the tenant's only project unless ``link`` is false —
    a no-op for none, and for a store an earlier build already seeded."""
    if not repositories:
        return
    authoring = config_authoring(
        engine, keys=hub_key_provider({}, data_dir=tmp_path), clock=FixedClock(datetime(2026, 7, 12, tzinfo=UTC))
    )
    for fields in repositories:
        name = f"{fields.owner}-{fields.repo}"
        existing = RepositoryRecordStore(hub_store_connections(engine)).resolve(name)
        if existing is not None and existing.fields.forge_api_url == fields.forge_api_url:
            continue
        if existing is not None:  # the same owner/repo on another forge is a record of its own
            name = f"{name}-{re.sub('[^a-z0-9]+', '-', urlparse(fields.forge_api_url).netloc.lower()).strip('-')}"
            if RepositoryRecordStore(hub_store_connections(engine)).resolve(name) is not None:
                continue
        with contextlib.suppress(SecretAlreadyExists):
            authoring.create_secret(SecretName.parse(fields.secret_name), "fixture-token", OP)
        authoring.create_repository(name, None, fields, OP)
        if link:
            link_project(engine, repository=name)


def seed_fake_work_sources(engine: Engine, names: Iterable[str], *, tmp_path: Path, link: bool = True) -> None:
    """Store a configured work source for each of ``names`` — the record an operator's ``blizzard hub source
    create`` writes, on a locator of its own — and, with ``link``, link it to the tenant's only project, so an
    ingest from the fake registry's source of that name resolves a project. A no-op for a name already stored.

    Each record's id is its name, not a minted ``wsrc_`` id: a fake source's pointers carry its name as their
    ``source``, and that must be the stored source's key for the links, labels and invariants that read it."""
    stores = WorkSourceRecordStore(hub_store_connections(engine))
    at = datetime(2026, 7, 12, tzinfo=UTC)
    authoring = config_authoring(
        engine, keys=hub_key_provider({}, data_dir=tmp_path), clock=FixedClock(datetime(2026, 7, 12, tzinfo=UTC))
    )
    for name in names:
        if stores.resolve(name) is not None:
            continue
        with contextlib.suppress(SecretAlreadyExists):
            authoring.create_secret(SecretName.parse(FIXTURE_FORGE_SECRET), "fixture-token", OP)
        fields = WorkSourceFields(
            provider="github",
            locator=f"fixture/{name}",
            api_base="http://forge.fixture",
            web_base=None,
            annotate=False,
            secret=FIXTURE_FORGE_SECRET,
        )
        record, change = ConfiguredWorkSource.new(name, None, fields, OP, at=at, work_source_id=WorkSourceId(name))
        stores.create(record, change=change)
        if link:
            link_project(engine, source=name)


def build_hub(
    tmp_path: Path,
    *,
    work_sources: dict[str, FakeWorkSource] | None = None,
    link: bool = True,
    repositories: Sequence[RepositoryFields] | None = None,
    public_url: str | None = None,
    route_token_mode: str = ROUTE_TOKEN_WARN,
    produces_mode: str = PRODUCES_WARN,
    follow_latest: bool = False,
    auth_mode: str = AUTH_MODE_NONE,
    superuser: str | None = None,
    login_throttle: LoginThrottleConfig = LoginThrottleConfig(),
    oauth_providers: dict[str, IOAuthProvider] | None = None,
    trusted_proxies: Sequence[str] = (),
    transcript_caps: TranscriptCaps | None = None,
    system_artifacts: PackagedSystemArtifacts | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingConfig | None = None,
    tracing_settings: TracingSettings | None = None,
    egress: EgressConfig | None = None,
    egress_path_key: bytes | None = None,
    auto_register_claimants: bool = True,
    flags: Flags | None = None,
    hub_version: str | None = None,
    version_window: VersionWindow = VERSION_WINDOW,
    actions: HubActions = TEST_HUB_ACTIONS,
    action_pass_seconds: float = DEFAULT_ACTION_PASS_SECONDS,
    action_pass_grace: timedelta = ACTION_PASS_GRACE,
    forges: ForgeRegistryFactory = default_forge_registry,
) -> HubHarness:
    """A migrated, fully-wired hub over ``tmp_path`` with fake external seams.

    ``hub.client`` is a :class:`RunnerFleetClient`.
    ``work_sources=None`` defaults to one fake source, while ``{}`` is a deliberately **empty** registry;
    each fake source is stored as a configured record linked to the tenant's only project, and each seeded
    repository is linked to it too, while ``link=False`` leaves the sources unstored, serving no project, as a
    file-configured source is, and the repositories stored but unlinked; ``hub_version`` and ``version_window`` default to the installed and packaged."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    config = HubConfig(
        root=tmp_path,
        db_url=db_url,
        route_token_mode=route_token_mode,
        produces_mode=produces_mode,
        follow_latest=follow_latest,
        auth=AuthConfig(mode=auth_mode, superuser=superuser, login_throttle=login_throttle),
        trusted_proxies=tuple(trusted_proxies),
        tracing=tracing or TracingConfig(),
        egress=egress or EgressConfig(),
    )
    # A second build over the same ``tmp_path`` reopens the store the first one wrote;
    # copying over it would discard that state, so only a fresh directory takes the copy.
    db_path = tmp_path / "hub.db"
    if db_path.exists():
        Migrations(config, MIGRATION_CLOCK).runner.upgrade("head")
    else:
        shutil.copyfile(hub_migration_prototype(), db_path)
    engine = create_engine_from_url(db_url)
    seed_repositories(
        engine, DEFAULT_FIXTURE_REPOSITORIES if repositories is None else repositories, tmp_path=tmp_path, link=link
    )

    built_sources: dict[str, IWorkSource] = dict(
        work_sources if work_sources is not None else {"default": FakeWorkSource()}
    )
    if link:
        seed_fake_work_sources(engine, built_sources, tmp_path=tmp_path)
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    editors: dict[str, IWorkEditor] = {}
    # The built-in `hub` source is seated as a source, an editor and a closer unconditionally,
    # mirroring `WorkSourceEntry.registry`'s production wiring.
    closers: dict[str, IWorkCloser] = {}
    core = build_hub_core(engine, clock=clock)
    default = StoreScope(default_tenant(engine))
    default_stores = core.open(default)
    hub_source = HubWorkSource(
        default_stores.work_item_store,
        default_stores.work_item_edits,
        core.users,
        default_stores.garden_proposal_resolution,
    )
    built_sources[RESERVED_HUB_SOURCE_NAME] = hub_source
    editors[RESERVED_HUB_SOURCE_NAME] = hub_source
    closers[RESERVED_HUB_SOURCE_NAME] = hub_source
    work_source_registry = WorkSourceRegistry(built_sources, closers=closers, editors=editors)

    def tenant_work_sources(stores: TenantStores, _live: LiveConfig) -> IWorkSourceRegistry:
        # The default tenant reads the fakes; any other tenant holds only its own built-in source.
        if stores.scope == default:
            return work_source_registry
        source = HubWorkSource(
            stores.work_item_store, stores.work_item_edits, core.users, stores.garden_proposal_resolution
        )
        named = {RESERVED_HUB_SOURCE_NAME: source}
        return WorkSourceRegistry(dict(named), closers=dict(named), editors=dict(named))

    secret_keys = hub_key_provider({}, data_dir=tmp_path)
    hub_services = build_services(
        core,
        work_sources=tenant_work_sources,
        public_url=public_url,
        oauth_registry=OAuthProviderRegistry(oauth_providers) if oauth_providers is not None else None,
        # The IdP signing-key lifecycle — wired only under `oauth`, mirroring
        # `src/blizzard/hub/app.py`'s own `build_hosted_app` gating exactly.
        signing_keys_dir=(tmp_path / "auth" / "signing-keys") if auth_mode == AUTH_MODE_OAUTH else None,
        secret_keys=secret_keys,
        trusted_proxies=TrustedProxies.parse(config.trusted_proxies),
        login_throttle=login_throttle,
        transcript_caps=transcript_caps,
        system_artifacts=system_artifacts,
        trace_exporter=trace_exporter,
        tracing_settings=tracing_settings,
        tracing=config.tracing,
        egress=config.egress,
        egress_path_key=egress_path_key,
        annotation_interval_seconds=config.annotation_interval_seconds,
        hub_version=hub_version or __version__,
        version_window=version_window,
        actions=actions,
        action_pass_seconds=action_pass_seconds,
        action_pass_grace=action_pass_grace,
        forges=forges,
    )
    hub_services.tenants.open_tenants.refresh()
    services = hub_services.tenants.open_services(default)
    app = create_hub_app(config, services=hub_services, flags=flags)
    client = RunnerFleetClient(app, services=services, register_claimants=auto_register_claimants)
    # Warm FastAPI's per-router route-resolution cache: it lazily caches routes on first
    # use, which is thread-unsafe under the component tier's OS-thread races.
    client.get("/api/_route_cache_warm")
    return HubHarness(
        client=client,
        services=services,
        hub_services=hub_services,
        work_sources=work_source_registry,
        clock=clock,
        engine=engine,
        events=services.events,
        app=app,
    )


#: The one secret every records-seeded fixture source names — no fixture forge checks a token.
FIXTURE_FORGE_SECRET = "fixture-forge-token"


def fixture_work_source(slug: str, locator: str, api_base: str, *, annotate: bool = False) -> WorkSourceDocument:
    """A GitHub-shaped source on a fixture forge, naming :data:`FIXTURE_FORGE_SECRET`."""
    return WorkSourceDocument(
        slug=slug, provider="github", locator=locator, api_base=api_base, annotate=annotate, secret=FIXTURE_FORGE_SECRET
    )


def ensure_fixture_secret(hub: httpx2.Client) -> None:
    """Create :data:`FIXTURE_FORGE_SECRET` unless an earlier seeding already did."""
    secret = hub.post("/api/secrets", json={"name": FIXTURE_FORGE_SECRET, "value": "fixture-token"})
    assert secret.status_code in (201, 409), secret.text


def fixture_repository(name: str, forge_api_url: str, *, owner: str, base_branch: str = "main") -> dict[str, str]:
    """A repository record body on a fixture forge, naming :data:`FIXTURE_FORGE_SECRET`."""
    return {
        "slug": name,
        "forge_api_url": forge_api_url,
        "owner": owner,
        "repo": name,
        "base_branch": base_branch,
        "secret_name": FIXTURE_FORGE_SECRET,
    }


def fixture_repositories(sources: Sequence[WorkSourceDocument], forge_api_url: str) -> list[dict[str, str]]:
    """One repository record per source's ``owner/repo`` locator, on the fixture forge's ``main``."""
    return [
        fixture_repository(source.locator.rpartition("/")[2], forge_api_url, owner=source.locator.partition("/")[0])
        for source in sources
    ]


def create_repositories(
    hub: httpx2.Client, repositories: Sequence[dict[str, str]], *, project: str | None = "default"
) -> None:
    """Create repository ``repositories`` — and the secret they name — through a running hub's own
    API and link each to ``project`` (a slug; ``None`` links none), so a deliver node of that project's
    chunks resolves their commits against them."""
    ensure_fixture_secret(hub)
    for repository in repositories:
        created = hub.post("/api/repositories", json=repository)
        assert created.status_code == 201, created.text
        if project is not None:
            linked = hub.put(f"/api/projects/{project}/repository-links/{repository['slug']}")
            assert linked.status_code == 200, linked.text


def create_work_sources(hub: httpx2.Client, sources: Sequence[WorkSourceDocument]) -> None:
    """Create ``sources``, and the secret they name, through a running hub's own API, each linked to the
    hub's only project.

    Every upper-tier fixture that stands a real hub up and then ingests seeds its sources
    through this once the hub answers, or its own ingests fail."""
    if not sources:
        return
    ensure_fixture_secret(hub)
    projects = hub.get("/api/projects")
    assert projects.status_code == 200, projects.text
    (project,) = projects.json()["projects"]
    for source in sources:
        created = hub.post("/api/work-sources", json=source.model_dump())
        assert created.status_code == 201, created.text
        linked = hub.put(f"/api/projects/{project['project_id']}/source-links/{source.slug}")
        assert linked.status_code == 200, linked.text


def write_mock_harness_credentials(runner_dir: Path) -> tuple[str, str]:
    """Fixture-controlled, always-valid credential files for the harness-health probes
    — neither mock CLI is a real, logged-in provider, so a runner driven
    against them needs its own disposable stand-ins rather than reading whatever (if
    anything) sits at each probe's real-credential-store default on this machine.
    Returns ``(claude_code_credentials_path, opencode_auth_path)``."""
    claude_credentials = runner_dir / "mock-claude-credentials.json"
    claude_credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "mock-token"}}))
    opencode_auth = runner_dir / "mock-opencode-auth.json"
    opencode_auth.write_text(json.dumps({"anthropic": {"type": "oauth"}}))
    return str(claude_credentials), str(opencode_auth)


def daemon_log_sink(path: Path) -> IO[str]:
    """An append-mode file for a spawned daemon's merged stdout/stderr.

    A long-lived daemon must NEVER get ``stdout=PIPE`` (``bzh:daemon-stdout-to-file``):
    nothing here drains it, so the daemon wedges once the pipe buffer fills, surfacing
    as an unrelated timeout far from the cause."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", buffering=1)


def read_daemon_log(path: Path | None) -> str:
    """A spawned daemon's log text, or a legible stand-in — the early-exit diagnostic.

    Total by design: a daemon that died before its log file existed must still produce a
    message naming what happened, so a missing/unreadable file degrades to a note rather
    than masking the failure with an :class:`OSError` of its own."""
    if path is None:
        return "<no log file>"
    try:
        return path.read_text()
    except OSError as exc:  # pragma: no cover - defensive, see docstring
        return f"<log {path} unreadable: {exc}>"


@functools.lru_cache(maxsize=1)
def shared_daemon_log_dir() -> Path:
    """The per-process fallback log directory for daemons spawned with no runtime dir.

    The mock fleet's daemons own no runtime directory to put a log beside, and threading
    one through their ~40 call sites buys nothing a single well-named directory does not.
    Created once per pytest process and reused."""
    return Path(tempfile.mkdtemp(prefix="blizzard-daemon-logs-"))


_PORT_FLOOR = 20000
_PORT_CEILING = 32768  # the kernel's ephemeral range starts here
_PORT_BAND = 256
_port_lock = threading.Lock()
_port_cursor: list[int] = []


def _worker_port_band() -> int:
    """This worker's first port: bands are disjoint within a run and shifted per run."""
    run_id = os.environ.get("PYTEST_XDIST_TESTRUNUID") or f"{os.getpid()}-{time.time_ns()}"
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    bands = (_PORT_CEILING - _PORT_FLOOR) // _PORT_BAND
    run_offset = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:4], "big")
    return _PORT_FLOOR + ((run_offset + int(worker.removeprefix("gw"))) % bands) * _PORT_BAND


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def free_port() -> int:
    """A localhost port no other xdist worker of this run can be handed before its daemon binds."""
    with _port_lock:
        if not _port_cursor:
            _port_cursor.append(_worker_port_band())
        band = _port_cursor[0] - (_port_cursor[0] - _PORT_FLOOR) % _PORT_BAND
        for _ in range(_PORT_BAND):
            port = _port_cursor[0]
            _port_cursor[0] = band + (port - band + 1) % _PORT_BAND
            if _port_is_free(port):
                return port
    raise RuntimeError(f"no free port in the band starting at {band}")


def parse_sse_frames(text: str) -> list[dict[str, str]]:
    """Parse an ``text/event-stream`` payload into ``[{id, event, data}]`` dicts.

    Reserved comment lines (``:``-prefixed) and keepalives are skipped; a blank line
    terminates one frame.
    """
    events: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith(":"):
            continue  # a comment (reserved / keepalive)
        if line.startswith("id:"):
            current["id"] = line[3:].strip()
        elif line.startswith("event:"):
            current["event"] = line[6:].strip()
        elif line.startswith("data:"):
            current["data"] = line[5:].strip()
        elif line == "" and "event" in current:
            events.append(current)
            current = {}
    if "event" in current:
        events.append(current)
    return events


async def drain_stream(broker: EventBroker, *, last_event_id: int = 0) -> list[dict[str, str]]:
    """Read the SSE endpoint's own generator to the end of its replay tail (a real stream read).

    Starlette's ``TestClient`` buffers a whole response body, so it cannot consume an
    infinite live stream incrementally. Instead this drives the route's async generator
    directly with a request that reports itself disconnected, emitting the replay tail."""
    from blizzard.hub.api.events import _RESERVED_COMMENT, Cursor, Stream

    class _DisconnectedRequest:
        async def is_disconnected(self) -> bool:
            return True

    chunks: list[bytes] = []
    stream = Stream(broker, _DisconnectedRequest(), Cursor(last_event_id), _RESERVED_COMMENT)  # type: ignore[arg-type]
    async for chunk in stream.frames():
        chunks.append(chunk)
    return parse_sse_frames(b"".join(chunks).decode())


def emitted_events(hub: HubHarness, *, since: int = 0) -> list[dict[str, str]]:
    """The typed events the hub published after ``since`` — the broker's replay tail.

    Asserting on it asserts SSE emission without the buffering-transport limitation
    :func:`drain_stream` describes. Each dict carries ``id``, ``event``, ``data``.
    """
    return [{"id": str(e.id), "event": e.type, "data": e.data} for e in hub.events.replay_since(since)]


def pointer_token(pointer: dict) -> str:
    """A ``{source, ref}`` pointer dict's own ``{source}:{ref}`` ingest token —
    the request-side shape a test builds from the same dict it asserts the response
    (``{source, ref, label, web_url}``) against."""
    return f"{pointer['source']}:{pointer['ref']}"


def ingest(hub: HubHarness, pointers: list[dict], *, promote: bool = True, project: str | None = None) -> str:
    """Ingest ``pointers`` (as ``{source, ref}`` dicts) into one chunk and, by default,
    promote it to ready — each dict converts to its ``{source}:{ref}`` ingest token.
    ``project`` names the project by id or slug; ``None`` leaves the hub to infer it.

    Pass ``promote=False`` to assert the not-ready resting state a bare ingest leaves."""
    body: dict[str, Any] = {"tokens": [pointer_token(p) for p in pointers]}
    if project is not None:
        body["project"] = project
    resp = hub.client.post("/api/chunks", json=body)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    if promote:
        promoted = hub.client.post(f"/api/chunks/{chunk_id}/promote")
        assert promoted.status_code == 202, promoted.text
    return chunk_id


def chunk_facts_of(hub: HubHarness, chunk_id: str) -> ChunkFacts:
    """A chunk's current facts, or the unminted default — the same fallback every route
    applies, for a test calling a write-verb domain service directly rather than through
    its route (which would otherwise supply this from its own ``ChunkChanged.before``)."""
    return ChunkFacts.or_default(hub.services.chunks.facts.load_facts(chunk_id))


def write_chunk_pause_facts(tmp_path: Path, chunk_id: str, *facts: tuple[bool, datetime]) -> None:
    """Append ``chunk_pause_facts`` rows for ``chunk_id``, in argument order.

    Not a stand-in for the pause route: this exists for the one thing it cannot express
    — **arbitrary ``set_at`` values**, since the route stamps a single ``clock.now()``.
    Each tuple is ``(paused, set_at)``; write order is the newest-wins order."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    with engine.begin() as conn:
        for paused, set_at in facts:
            conn.execute(
                sa_insert(schema.chunk_pause_facts).values(
                    stamp(schema.chunk_pause_facts, chunk_id=chunk_id, paused=paused, set_at=set_at, set_by="operator")
                )
            )


def seed_user(
    hub: HubHarness, *, username: str, role: Role, email: str | None = None, display_name: str | None = None
) -> User:
    """Insert one ``users`` row directly (a raw-write test helper, mirrors
    ``write_chunk_pause_facts``) with what ``role`` stands for, and return the domain object:
    ``guest``, ``contributor`` or ``admin`` a membership of that role in the default tenant,
    ``superuser`` an ``admin`` membership there and the hub administrator role, ``pending`` neither.

    A test wanting a ``ResolvedIdentity`` seeds the user directly rather than through a
    sign-in."""
    user = User(
        user_id=Id.mint(IdPrefix.USER, hub.clock).value,
        username=username,
        display_name=display_name or username,
        email=email,
        created_at=hub.clock.now(),
    )
    now = hub.clock.now()
    membership = Role.ADMIN if role is Role.SUPERUSER else role
    with hub.engine.begin() as conn:
        conn.execute(
            sa_insert(schema.users).values(
                id=user.user_id,
                username=user.username,
                display_name=user.display_name,
                email=user.email,
                created_at=user.created_at,
            )
        )
        if membership in MEMBERSHIP_ROLES:
            conn.execute(
                sa_insert(schema.membership_facts).values(
                    tenant_id=default_tenant(hub.engine),
                    user_id=user.user_id,
                    role=membership.value,
                    set_at=now,
                    set_by="migration",
                )
            )
        if role is Role.SUPERUSER:
            conn.execute(
                sa_insert(schema.hub_admin_facts).values(
                    user_id=user.user_id, granted=True, set_at=now, set_by="migration"
                )
            )
    return user


#: When the seeding helpers below record a fact unless a test names its own time: the harness clock's start.
_SEEDED_AT = datetime(2026, 7, 13, tzinfo=UTC)

#: How long a seeded invitation stays live unless a test names its own lifetime — the hub's default.
_INVITATION_LIFETIME = timedelta(days=7)


def seed_membership(
    engine: Engine, tenant: TenantId, user_id: str, role: Role, *, set_by: str = "test", at: datetime = _SEEDED_AT
) -> None:
    """Record ``user_id`` as a ``role`` member of ``tenant`` — a raw ``membership_facts`` row, as a grant writes."""
    assert role in MEMBERSHIP_ROLES, f"{role} is not a membership role"
    with engine.begin() as conn:
        conn.execute(
            sa_insert(schema.membership_facts).values(
                tenant_id=tenant, user_id=user_id, role=role.value, set_at=at, set_by=set_by
            )
        )


def seed_hub_admin(engine: Engine, user_id: str, *, set_by: str = "test", at: datetime = _SEEDED_AT) -> None:
    """Grant ``user_id`` the hub administrator role — a raw ``hub_admin_facts`` row, holding no membership."""
    with engine.begin() as conn:
        conn.execute(sa_insert(schema.hub_admin_facts).values(user_id=user_id, granted=True, set_at=at, set_by=set_by))


def seed_invitation(
    engine: Engine,
    tenant: TenantId,
    email: str,
    role: Role,
    *,
    created_by: str = "test",
    at: datetime = _SEEDED_AT,
    lifetime: timedelta = _INVITATION_LIFETIME,
) -> tuple[str, str]:
    """Record a live invitation admitting ``email`` into ``tenant`` as ``role``, issued by ``created_by`` at ``at``
    and expiring ``lifetime`` later, as the hub stores one: the address lowercased, the token only as its hash.
    Returns the invitation's id and the link's plaintext token."""
    assert role in MEMBERSHIP_ROLES, f"{role} is not a membership role"
    invitation_id = Id.mint_at(IdPrefix.INVITATION, at).value
    token = f"test-invitation-{invitation_id}"
    with engine.begin() as conn:
        conn.execute(
            sa_insert(schema.invitations).values(
                invitation_id=invitation_id,
                tenant_id=tenant,
                email=email.lower(),
                role=role.value,
                token_hash=InvitationToken(token).hash,
                created_by=created_by,
                created_at=at,
                expires_at=at + lifetime,
            )
        )
    return invitation_id, token


def seed_session(hub: HubHarness, user: User) -> str:
    """Mint a real session for ``user`` via ``AuthService.mint_session`` (#92) and return
    the plaintext session id — a test sets this as the ``bz_session`` cookie or an
    ``Authorization: Bearer`` header."""
    plaintext, _ = hub.hub_services.auth.mint_session(user)
    return plaintext


def assert_utc_iso(value: object) -> None:
    """Assert ``value`` is a literal ISO-8601 string carrying an explicit UTC offset.

    Pins the wire **bytes**, not a parsed-then-compared value
    (``bzh:utc-instants``): a naive string re-parses fine on the same box that emitted it,
    so only the literal trailing designator (``+00:00`` / ``Z``) catches the bug."""
    assert isinstance(value, str), f"expected an ISO-8601 timestamp string, got {value!r}"
    assert value.endswith("+00:00") or value.endswith("Z"), f"timestamp missing a UTC offset: {value!r}"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def assert_all_timestamps_utc(payload: object) -> None:
    """Recursively walk a response body, applying :func:`assert_utc_iso` to every ``*_at`` key.

    A route test calls this once on its response; a route that later adds a seventh
    timestamp field is covered without the test itself changing.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith("_at") and value is not None:
                assert_utc_iso(value)
            else:
                assert_all_timestamps_utc(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_all_timestamps_utc(item)


def seed_chunk_record(stores: ChunkStores, chunk: Chunk) -> None:
    """Insert ``chunk``'s rows the way ingest does — under its pointers' locks — for a fixture
    that needs a chunk without going through an ingest's guard. A chunk built naming no project
    lands in the tenant's only one."""
    with stores.exclusive.locked_work_refs(chunk.work_refs) as handle:
        if chunk.project_id is None:
            chunk = replace(chunk, project_id=_only_project_on(work_ref_conn_of(handle)))
        stores.record.mint_locked(handle, chunk)


def _only_project_on(conn: sa.Connection) -> ProjectId:
    """The id of the only project of the tenant ``conn`` is scoped to; raises when it holds none or several."""
    projects = schema.projects
    ids = list(conn.execute(statements_of(conn).select(projects, projects.c.project_id)).scalars())
    if len(ids) != 1:
        raise AssertionError(f"a chunk naming no project needs a tenant holding exactly one; it holds {len(ids)}")
    return ProjectId(ids[0])


def make_ready(hub: HubHarness, chunk_id: str) -> None:
    """Promote ``chunk_id`` through the promote service — a replay once promoted — so a claim
    finds it ``ready``. Bypasses the HTTP route: no operator session and no SSE frame."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, f"unknown chunk {chunk_id}"
    hub.services.promote.promote(chunk, statuses=hub.services.chunks.facts.load_live_statuses())


def claim_route(hub: HubHarness, chunk_id: str, *, runner_id: str = "r1") -> dict:
    """Promote ``chunk_id`` (a replay once promoted) and claim it for ``runner_id`` through POST
    /routes — the route a runner must hold before its first ``lease.minted`` above the claim's
    reservation is admitted. Only a ``ready`` chunk is claimable."""
    make_ready(hub, chunk_id)
    resp = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def report_lease(
    hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, runner_id: str = "r1", route_token: str | None = None
) -> dict:
    """Report a runner-minted ``lease.minted`` fact through POST /events.

    A component test calls this first so the hub knows the chunk's latest epoch.
    ``route_token`` rides the payload; ``None`` omits it, matching a caller
    that never claimed under the plaintext."""
    payload: dict[str, object] = {"chunk_id": chunk_id, "epoch": epoch}
    if route_token is not None:
        payload["route_token"] = route_token
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "lease.minted", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def report_escalation(
    hub: HubHarness,
    chunk_id: str,
    *,
    epoch: int,
    seq: int,
    runner_id: str = "r1",
    takeover_command: str = "cd env && claude --resume s",
    **fields: object,
) -> dict:
    """Report a runner's ``escalation.recorded`` fact through POST /events — the chunk derives
    ``needs_human`` when the fence admits it. ``fields`` rides the payload verbatim
    (``lease_id``, ``wrapped_takeover_command``, ``cause``, ``detail``, ``route_token``); the
    caller reads ``applied``/``rejected`` off the returned ack."""
    payload: dict[str, object] = {"chunk_id": chunk_id, "epoch": epoch, "takeover_command": takeover_command, **fields}
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "escalation.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def report_question(
    hub: HubHarness, chunk_id: str, *, epoch: int, seq: int, question_id: str = "q_1", runner_id: str = "r1"
) -> dict:
    """Report a runner's ``question.asked`` fact through POST /events; the caller reads
    ``applied``/``rejected`` off the returned ack."""
    payload = {"chunk_id": chunk_id, "question_id": question_id, "epoch": epoch, "question": "which?"}
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "question.asked", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def chunk_rows(hub: HubHarness, table: sa.Table, chunk_id: str) -> int:
    """How many rows ``table`` holds for ``chunk_id`` — a refusal's proof that nothing was written."""
    with hub.engine.begin() as conn:
        return conn.execute(
            hub_scoped(sa.select(sa.func.count()).select_from(table).where(table.c.chunk_id == chunk_id))
        ).scalar_one()


# Migration-test scaffolding: `graphs`/`chunks` carry no revision-pinned shape, so these
# seeds are shared. A revision's own frozen table-under-test must NOT move here. `tenant_id`
# and `project_id` are written only when the store the seed runs against has the column.

_GRAPHS = sa.Table(
    "graphs",
    sa.MetaData(),
    sa.Column("graph_id", sa.String, primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("entry_node_id", sa.String, nullable=False),
    sa.Column("definition_yaml", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime, nullable=False),
    sa.Column("tenant_id", sa.String),
)

_CHUNKS = sa.Table(
    "chunks",
    sa.MetaData(),
    sa.Column("chunk_id", sa.String, primary_key=True),
    sa.Column("graph_id", sa.String, nullable=False),
    sa.Column("minted_at", sa.DateTime, nullable=False),
    sa.Column("tenant_id", sa.String),
    sa.Column("project_id", sa.String),
)


def _stamp_as_stored(conn: sa.Connection, table: sa.Table, values: dict[str, Any]) -> dict[str, Any]:
    """``values`` carrying the store's only tenant when the store's own ``table`` has a ``tenant_id``
    column, and that tenant's only project when it has a ``project_id`` column — decided by the
    store's revision, not today's schema, so the seed fits any revision."""
    stored = {column["name"] for column in sa.inspect(conn).get_columns(table.name)}
    if "tenant_id" in stored:
        values["tenant_id"] = only_tenant_subquery()
    if "project_id" in stored:
        values["project_id"] = only_project_subquery()
    return values


def migrate_to(tmp_path: Path, revision: str) -> tuple[MigrationRunner, Engine]:
    """A hub store migrated to ``revision``, ready for a test's own revision-pinned seed
    rows. The returned runner is the same handle a test upgrades onward from (e.g. to
    ``"head"``) once its seed is in place. The engine is plain, so the tenant statement guard
    watches every adapter a test wires over it; a seed written at a pinned revision through
    today's ``schema`` tables, outside any tenant scope, goes through ``hub_scoped_engine``."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), MIGRATION_CLOCK).runner
    if revision == "head" and not (tmp_path / "hub.db").exists():
        migrated_hub_at(db_url)
    else:
        runner.upgrade(revision)
    return runner, create_engine_from_url(db_url)


def upgrade_runner_store_as_registered(
    migrations: MigrationRunner, db_url: str, runner_id: str = "rn_01JX0000000000000000000000"
) -> None:
    """Upgrade a runner store seeded at an old revision to head with ``runner_id`` registered: the tightening revision
    refuses rows naming no runner unless the identity row can name one, so the identity lands between the additive
    revision (which creates the column) and the tightening one (which claims the old rows for it)."""
    migrations.upgrade("20261011_1000_runner_scope")
    engine = create_engine_from_url(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO runner_identity (runner_id, runner_name, registered_at)"
                    " VALUES (:id, 'r', '2026-10-01 00:00:00')"
                ),
                {"id": runner_id},
            )
    finally:
        engine.dispose()
    migrations.upgrade("head")


def seed_graph(conn: sa.Connection, graph_id: str, *, at: datetime) -> None:
    """Seed one ``graphs`` parent row — the FK a seeded chunk needs, at any revision."""
    values = {"graph_id": graph_id, "name": "g", "entry_node_id": "nd_1", "definition_yaml": "", "created_at": at}
    conn.execute(sa.insert(_GRAPHS).values(_stamp_as_stored(conn, _GRAPHS, values)))


def mint_graph(engine: Engine, graph_id: str, *, name: str, nodes: dict[str, str], at: datetime) -> None:
    """Mint one graph with real, named nodes through the graph store's own write path —
    ``nodes`` maps node id to node name, the first being the entry. ``seed_graph`` writes no
    ``graph_nodes``, so a test that needs names to resolve mints through here."""
    node_rows = [
        Node(
            node_id=node_id,
            graph_id=graph_id,
            name=node_name,
            executor=Executor.RUNNER,
            prompt="p",
            checks=[],
            produces=[],
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=None,
            retries_exhausted=None,
        )
        for node_id, node_name in nodes.items()
    ]
    graph = Graph(
        graph_id=graph_id, name=name, entry_node_id=node_rows[0].node_id, nodes=node_rows, edges=[], created_at=at
    )
    GraphStore(hub_store_connections(engine)).mint(graph, definition_yaml="", at=at)


def seed_chunk(
    conn: sa.Connection, chunk_id: str, *, graph_id: str, at: datetime, project_id: str | None = None
) -> None:
    """Seed one ``chunks`` parent row — the FK a seeded route/pointer/etc. needs — in ``project_id``, the
    tenant's only project when ``None``."""
    values: dict[str, Any] = {"chunk_id": chunk_id, "graph_id": graph_id, "minted_at": at}
    if project_id is not None:
        values["project_id"] = project_id
    conn.execute(sa.insert(_CHUNKS).values(_stamp_as_stored(conn, _CHUNKS, values)))


def seed_lease(engine: Engine, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
    """Seed one hub ``lease_facts`` row with its epoch's owner — a runner's, or the hub's for
    ``runner_id`` ``hub`` — the shape an admitted mint leaves, for a test that only needs the
    chunk's fence at ``epoch``. Head-schema only."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(schema.lease_facts).values(
                stamp(schema.lease_facts, chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
        )
        conn.execute(
            sa.insert(schema.epoch_owners).values(
                stamp(
                    schema.epoch_owners,
                    chunk_id=chunk_id,
                    epoch=epoch,
                    runner_id=None if runner_id == "hub" else runner_id,
                    recorded_at=at,
                )
            )
        )


def seed_work_item(
    store: IWriteWorkItemRepository,
    *,
    source: str = "hub",
    graph_id: str,
    title: str = "t",
    body: str = "b",
    author: WorkItemAuthor,
    stated_priority: str | None = None,
    at: datetime,
    project_id: ProjectId | None = None,
) -> HubWorkItem:
    """Seed one hub-owned work item plus its resting chunk in ``project_id`` — the store's tenant's only
    project when ``None`` — mirroring production's own two-step mint (``WorkItemEditService.create``):
    there is no chunkless filing path to seed around. Callers still seed ``graph_id``'s own row first
    (``seed_graph``); this only seeds the item and its chunk."""
    ref = store.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    project = project_id if project_id is not None else _only_project_of_items(store)
    chunk = Chunk(chunk_id=f"ch_{ref}", graph_id=graph_id, work_refs=[pointer], minted_at=at, project_id=project)
    return store.create_with_chunk(
        pointer=pointer, title=title, body=body, author=author, stated_priority=stated_priority, at=at, chunk=chunk
    )


def _only_project_of_items(store: IWriteWorkItemRepository) -> ProjectId:
    """The only project of the tenant ``store`` writes, read through the adapter's own connections; raises when
    the tenant holds none or several, or when ``store`` is not the store's adapter."""
    if not isinstance(store, WorkItemStore):
        raise TypeError(f"a work item seeded naming no project needs a WorkItemStore; got {type(store).__name__}")
    return ProjectId(_only_project_of(store._store))


@contextmanager
def capture_statements(engine: Engine) -> Iterator[list[tuple[str, Any]]]:
    """Every statement ``engine`` issues while the context is open, in issue order —
    statement text plus its bound parameters, ready to re-run later (e.g. under
    ``EXPLAIN QUERY PLAN``) with no drift from what was actually sent."""
    statements: list[tuple[str, Any]] = []

    def _listener(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        statements.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _listener)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _listener)


def count_queries(engine: Engine, fn: Callable[[], object]) -> int:
    """How many statements ``fn`` issues on ``engine`` — what a bulk-read test asserts is
    flat as the fleet grows, rather than growing per chunk."""
    with capture_statements(engine) as statements:
        fn()
    return len(statements)


def count_rows_read(engine: Engine, fn: Callable[[], object]) -> int:
    """How many rows ``fn``'s ``SELECT`` statements return on ``engine`` — the row-volume
    counterpart of :func:`count_queries`. A statement count stays flat while a read still
    fetches (and derives) a row per chunk ever minted; this is what a live-set read asserts
    is flat as only the terminal-chunk count grows (``bzh:live-set-read``). Each captured
    ``SELECT`` is re-counted on a separate connection with its exact parameters."""
    with capture_statements(engine) as statements:
        fn()
    total = 0
    with engine.connect() as conn:
        for statement, parameters in statements:
            if statement.lstrip().upper().startswith("SELECT"):
                total += conn.exec_driver_sql(f"SELECT count(*) FROM ({statement})", parameters).scalar_one()
    return total


def explain_query_plan(engine: Engine, statement: str, parameters: Any) -> Sequence[sa.Row[Any]]:
    """Runs ``EXPLAIN QUERY PLAN`` for one captured statement, re-executed with its exact
    captured parameters."""
    with engine.connect() as conn:
        return conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters).all()


_SCAN_OR_SEARCH = re.compile(r"^(SCAN|SEARCH) (\S+)(.*)$")
_TENANT_PREFIX_ONLY = re.compile(r"INDEX \S+ \(tenant_id=\?\)$")
_ALIASED_FROM_ITEM = re.compile(r"\b(?:FROM|JOIN)\s+(\w+)\s+AS\s+(\w+)", re.IGNORECASE)


def _statement_aliases(statement: str) -> dict[str, str]:
    """Maps every ``<table> AS <alias>`` from item in ``statement`` back to its real table —
    a plan row names the alias sqlite was given, not the table it reads (e.g. ``leases``
    joined to itself as ``later_escalation_leases`` for a self-correlated NOT EXISTS)."""
    return {alias: table for table, alias in _ALIASED_FROM_ITEM.findall(statement)}


def _offending_table(detail: str, tables: set[str], aliases: dict[str, str]) -> str | None:
    """``detail`` is a plan row's last column, e.g. ``SCAN t``, ``SEARCH t USING INDEX ix (...)``, or ``SEARCH t USING
    AUTOMATIC COVERING INDEX (...)``; ``t`` may itself be an alias, resolved back to its real table via ``aliases``.
    Offends when the resolved table names one of ``tables`` and the plan reads it whole: a bare scan, any search or scan
    through sqlite's own automatic index, a full walk of a named index (``SCAN t USING [COVERING] INDEX ix``), or a
    search that seeks only the tenant (``SEARCH t USING INDEX ix (tenant_id=?)``) — a scoped read always carries
    ``tenant_id = ?``, so a tenant-led index searched by that alone walks every row the tenant holds."""
    match = _SCAN_OR_SEARCH.match(detail)
    if match is None:
        return None
    verb, name, rest = match.groups()
    table = aliases.get(name, name)
    if table not in tables:
        return None
    if "AUTOMATIC" in rest or verb == "SCAN":
        return table
    if _TENANT_PREFIX_ONLY.search(rest):
        return table
    return None


def offending_index_scans(
    engine: Engine, statements: Iterable[tuple[str, Any]], tables: Iterable[str]
) -> list[tuple[str, sa.Row[Any]]]:
    """Re-runs every distinct captured ``SELECT`` in ``statements`` under ``EXPLAIN QUERY
    PLAN`` and returns ``(table, plan row)`` for each row that reads one of ``tables`` whole
    (:func:`_offending_table` names the plans) — the shared classifier a component-tier gate
    drives per store against its own table vocabulary."""
    vocabulary = set(tables)
    offenders: list[tuple[str, sa.Row[Any]]] = []
    seen: set[str] = set()
    for statement, parameters in statements:
        if not statement.lstrip().upper().startswith("SELECT"):
            continue
        if statement in seen:
            continue
        seen.add(statement)
        aliases = _statement_aliases(statement)
        for row in explain_query_plan(engine, statement, parameters):
            table = _offending_table(row.detail, vocabulary, aliases)
            if table is not None:
                offenders.append((table, row))
    return offenders


class InMemoryEgressWriter:
    """An in-process :class:`IEgressWriter` — holds placed batches and manifests, runs the shared row validation.

    ``fail`` set makes every write and commit return that failure."""

    def __init__(self) -> None:
        self.batches: list[EgressBatch] = []
        self.manifests: list[tuple[EgressPass, tuple[PlacedFile, ...]]] = []
        self.fail: EgressFailure | None = None

    def write(self, batch: EgressBatch) -> FilesWritten | EgressFailure:
        if self.fail is not None:
            return self.fail
        if (invalid := validate_batch(batch)) is not None:
            return invalid
        self.batches.append(batch)
        if not batch.rows:
            return FilesWritten(())
        schema = batch.schema
        return FilesWritten(
            (
                PlacedFile(
                    path=f"{schema.name}/v{schema.major_version}/date={batch.partition}/{len(self.batches)}",
                    dataset=schema.name,
                    version=schema.major_version,
                    partition=batch.partition,
                    rows=len(batch.rows),
                    first_position=batch.rows[0].position,
                    last_position=batch.rows[-1].position,
                    sha256="0" * 64,
                ),
            )
        )

    def commit_pass(self, egress_pass: EgressPass, placed: Sequence[PlacedFile]) -> ManifestCommitted | EgressFailure:
        if self.fail is not None:
            return self.fail
        self.manifests.append((egress_pass, tuple(placed)))
        return ManifestCommitted(f"_manifests/{len(self.manifests)}.json")


def _conforms_in_memory_egress_writer(x: InMemoryEgressWriter) -> IEgressWriter:
    return x


_DRAINABLE_SOURCE = "wsrc_github"


def seed_drainable_history(
    conn: sa.Connection, tenant: TenantId, *, graph_id: str, node_id: str, at: datetime, open_refs: int = 1
) -> None:
    """Give ``tenant`` a whole chunk history whose tables a deletion drains apart, every hub invariant holding over it
    while it is whole. Chunk ``ch_ran`` was claimed by runner ``r1`` (its route, token and lease at an epoch the runner
    owns), migrated by that runner's landing — which released the route — restarted at a hub-owned epoch, and completed
    with one work ref whose close was enqueued and retired; chunk ``ch_open`` holds ``open_refs`` work refs and nothing
    else. Both chunks pin ``graph_id``, the tenant's own, whose ``node_id`` a runner executes, and name the source
    the work source ``github`` (id ``wsrc_github``), which the tenant's only project linked at ``at``.
    Partway through a drain the epoch owners, route facts and close intents are gone while the restart, the migration,
    the completion and some work refs are not. Chunk ids are global: one store holds one such history."""
    later = at + timedelta(seconds=1)
    rows: list[tuple[sa.Table, dict[str, Any]]] = [
        (
            schema.work_sources,
            {
                "work_source_id": _DRAINABLE_SOURCE,
                "slug": "github",
                "name": "github",
                "provider": "github",
                "locator": "acme/app",
                "annotate": False,
                "revision": 1,
                "created_at": at,
                "created_by": "seed",
            },
        ),
        (schema.chunks, {"chunk_id": "ch_ran", "graph_id": graph_id, "minted_at": at, "model": "m"}),
        (schema.chunks, {"chunk_id": "ch_open", "graph_id": graph_id, "minted_at": at, "model": "m"}),
        (schema.epoch_owners, {"chunk_id": "ch_ran", "epoch": 1, "runner_id": "r1", "recorded_at": at}),
        (
            schema.route_created,
            {
                "route_id": "rt_ran",
                "chunk_id": "ch_ran",
                "runner_id": "r1",
                "workspace_id": "w",
                "created_at": at,
                "seq": 1,
            },
        ),
        (schema.route_token_minted, {"chunk_id": "ch_ran", "token_hash": "h", "seq": 2, "minted_at": at}),
        (schema.lease_facts, {"chunk_id": "ch_ran", "epoch": 1, "runner_id": "r1", "minted_at": at}),
        (
            schema.chunk_migrations,
            {
                "migration_id": "mg_ran",
                "chunk_id": "ch_ran",
                "from_node_id": node_id,
                "from_graph_id": graph_id,
                "to_graph_id": graph_id,
                "landed_node_id": node_id,
                "epoch": 1,
                "recorded_at": at,
            },
        ),
        (schema.route_released, {"chunk_id": "ch_ran", "released_at": later, "seq": 3}),
        (schema.epoch_owners, {"chunk_id": "ch_ran", "epoch": 2, "runner_id": None, "recorded_at": later}),
        (
            schema.chunk_restarts,
            {
                "chunk_id": "ch_ran",
                "graph_id": graph_id,
                "to_node_id": node_id,
                "epoch": 2,
                "restarted_by": "op",
                "recorded_at": later,
            },
        ),
        (schema.chunk_completed, {"chunk_id": "ch_ran", "completed_at": later, "completed_by": "op"}),
        (schema.chunk_work_refs, {"chunk_id": "ch_ran", "source": _DRAINABLE_SOURCE, "ref": "1"}),
        (
            schema.close_intents,
            {"chunk_id": "ch_ran", "source": _DRAINABLE_SOURCE, "ref": "1", "enqueued_at": later, "retired_at": later},
        ),
    ]
    for table, values in rows:
        conn.execute(hub_scoped(sa.insert(table)).values(stamp(table, tenant_id=tenant, **values)))
    projects = schema.projects.c
    project_id = conn.execute(
        hub_scoped(sa.select(projects.project_id).where(projects.tenant_id == tenant))
    ).scalar_one()
    link = {"tenant_id": tenant, "project_id": project_id, "work_source_id": _DRAINABLE_SOURCE}
    conn.execute(hub_scoped(sa.insert(schema.project_source_links)).values(**link, revision=1))
    conn.execute(
        hub_scoped(sa.insert(schema.project_source_link_facts)).values(**link, linked=True, set_at=at, set_by="seed")
    )
    if open_refs:
        conn.execute(
            hub_scoped(sa.insert(schema.chunk_work_refs)),
            [
                {"tenant_id": tenant, "chunk_id": "ch_open", "source": _DRAINABLE_SOURCE, "ref": f"o{n}"}
                for n in range(open_refs)
            ],
        )
