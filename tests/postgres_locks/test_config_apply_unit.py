"""The complete apply decides under the config key, including no-ops and dry runs."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.composition import build_config_authoring, build_tenant_stores
from blizzard.hub.domain.config.apply import ApplyEntryRefused
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door
from blizzard.hub.domain.config.forges import ForgeKindOffers
from blizzard.hub.domain.config_apply.entries import DeclaredRoutine, DeclaredScope, DocumentEntries
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.garden.routines import RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.hub.domain.projects.declarations import ProjectDeclaration
from blizzard.hub.domain.projects.model import ProjectEdit
from blizzard.hub.domain.projects.order import PositionFact
from blizzard.hub.secrets import hub_key_provider, secret_cipher
from blizzard.hub.store import apply_participants, schema
from blizzard.hub.store.internal import graph_store as graph_module
from blizzard.hub.store.internal import project_position_store, project_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.postgres_locks.test_project_landing_order import _seed
from tests.support import RecordedEvents, make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)
_CTX = ChangeContext("competitor", Door.API)


def _application(engine: sa.Engine, tenant: TenantId, tmp_path: Path) -> ConfigApplication:
    clock = FixedClock(_AT)
    stores = build_tenant_stores(scoped_connections(engine, tenant), clock=clock)
    cipher = secret_cipher(hub_key_provider({}, data_dir=tmp_path))
    offers = ForgeKindOffers()
    authoring = build_config_authoring(stores, cipher=cipher, events=RecordedEvents(), offers=offers)
    participants = apply_participants.ApplyParticipants(
        stores.connections, imports=stores.config_import, cipher=cipher, offers=offers, pick=lambda colors: colors[0]
    )
    return ConfigApplication(participants=participants, authoring=authoring, clock=clock)


def _wait_for_key(observer: sa.Engine, future: Future[Any]) -> None:
    deadline = monotonic() + 5
    with observer.connect() as monitor:
        while monotonic() < deadline:
            blocked = monitor.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                WHERE w.application_name = 'apply_waiter' AND h.application_name = 'apply_holder')
            """)
            ).scalar_one()
            if blocked:
                return
            assert not future.done(), "apply completed before the config key was released"
            Event().wait(0.02)
    pytest.fail("PostgreSQL did not report apply waiting on the config key")


@pytest.mark.parametrize("dry_run", [False, True], ids=["no-op", "dry-run-no-op"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_unchanged_apply_still_waits_for_the_config_key(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool, omit_lock: bool
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, _ = _seed(holder)
        app = _application(waiter, scope.scope.tenant, tmp_path)
        entries = DocumentEntries(
            projects=(
                ProjectDeclaration(
                    slug=project.slug, edit=ProjectEdit(name=project.name), sources=None, repositories=None
                ),
            )
        )
        if omit_lock:
            monkeypatch.setattr(apply_participants, "lock_config_authoring", lambda conn: None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with scope.read("hold_config_key") as conn:
                lock_config_authoring(conn)
                future = pool.submit(app.apply, entries, actor="test", dry_run=dry_run)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    _wait_for_key(observer, future)
            result = future.result(timeout=10)
        assert result.outcomes[0].op is None
        assert (result.apply_id is None) is dry_run
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("change", ["project", "position"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "dry-run"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_apply_waits_then_reconciles_fresh_facts(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str, dry_run: bool, omit_lock: bool
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, repos = _seed(holder)
        app = _application(waiter, scope.scope.tenant, tmp_path)
        entry = ProjectDeclaration(
            slug=project.slug,
            edit=ProjectEdit(name=project.name) if change == "project" else ProjectEdit(),
            sources=None,
            repositories=None,
            landing_order=() if change == "position" else None,
        )
        if omit_lock:
            monkeypatch.setattr(apply_participants, "lock_config_authoring", lambda conn: None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # The holder deliberately does not take the tenant row: otherwise admission masks a missing key.
            with scope.read("competing_config_change") as conn:
                lock_config_authoring(conn)
                if change == "project":
                    edited = project.edit(ProjectEdit(name="Changed"), _CTX, if_match=None, at=_AT)
                    assert edited is not None
                    moved, audit = edited
                    project_store.update_project(conn, moved, project.revision, None, audit)
                else:
                    project_position_store._PositionDecision(conn).record_positions(
                        project.project_id, [PositionFact(repos[0].repository_id, 0.0)], at=_AT, by="competitor"
                    )
                future = pool.submit(app.apply, DocumentEntries(projects=(entry,)), actor="test", dry_run=dry_run)
                if omit_lock:
                    result = future.result(timeout=5)
                    assert result.outcomes[0].op is None  # stale snapshot: the competitor has not committed
                else:
                    _wait_for_key(observer, future)
                conn.commit()
            if not omit_lock:
                result = future.result(timeout=10)
                assert result.outcomes[0].op is ChangeOp.EDIT
                if change == "position":
                    diff = result.outcomes[0].diff
                    assert [(item.field, item.old, item.new) for item in diff] == [("landing_order", ["api"], [])]

        stored = project_store.ProjectStore(scope).get(project.project_id)
        assert stored is not None
        if change == "project":
            assert stored.name == ("Changed" if omit_lock or dry_run else project.name)
        else:
            links = ProjectLinkStore(scope).repositories_of(project.project_id)
            position = next(link.position for link in links if link.repository_id == repos[0].repository_id)
            assert position == (0.0 if omit_lock or dry_run else None)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["mint", "default", "retire", "enable"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_graph_writers_take_the_config_key_before_graph_name(
    database_url: str, operation: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, _, _ = _seed(holder)
        graph = make_graph("gr_apply_proof", "apply-proof")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        if operation == "enable":
            GraphStore(scope).record_lifecycle(graph.graph_id, retired=True, at=_AT, by="test")
        graphs = GraphStore(scoped_connections(waiter, scope.scope.tenant))

        def write() -> None:
            if operation == "mint":
                graphs.mint(make_graph("gr_apply_new", graph.name), definition_yaml="", at=_AT)
            elif operation == "default":
                with graphs.locked_name(graph.name) as locked:
                    assert locked.get_enabled_by_name(graph.name) is not None
            else:
                graphs.record_lifecycle(graph.graph_id, retired=operation == "retire", at=_AT, by="test")

        if omit_lock:
            monkeypatch.setattr(graph_module, "lock_config_authoring", lambda conn: None)
        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("hold_config_key") as conn:
            lock_config_authoring(conn)
            future = pool.submit(write)
            if omit_lock:
                future.result(timeout=5)
            else:
                _wait_for_key(observer, future)
                # The waiter has not already taken the graph key: the holder can take it after config.
                lock_keys(conn, "graph_name", [graph.name])
            conn.commit()
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "dry-run"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_apply_checks_graph_after_waiting_for_retirement(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool, omit_lock: bool
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, _ = _seed(holder)
        graph = make_graph("gr_retire_guard", "retire-guard")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        app = _application(waiter, scope.scope.tenant, tmp_path)
        entries = DocumentEntries(
            scopes=(DeclaredScope("build", "", ScopeEdit(), project.slug),),
            routines=(DeclaredRoutine("build", graph.name, "build", (), None, (), RoutineEdit(), project.slug),),
        )
        if omit_lock:
            monkeypatch.setattr(apply_participants, "lock_config_authoring", lambda conn: None)
        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("retire_under_key") as conn:
            lock_config_authoring(conn)
            lock_keys(conn, "graph_name", [graph.name])
            conn.execute(
                scope.insert(schema.graph_lifecycle_facts).values(
                    graph_id=graph.graph_id, retired=True, set_at=_AT, set_by="test"
                )
            )
            future = pool.submit(app.apply, entries, actor="test", dry_run=dry_run)
            if omit_lock:
                result = future.result(timeout=5)
                assert any(o.kind.value == "routine" and o.op is ChangeOp.CREATE for o in result.outcomes)
            else:
                _wait_for_key(observer, future)
            conn.commit()
            if not omit_lock:
                with pytest.raises(ApplyEntryRefused, match="no enabled graph named 'retire-guard'") as refused:
                    future.result(timeout=10)
                assert refused.value.section == "routines"
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "dry-run"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_graph_retirement_waits_for_apply_guard(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool, omit_lock: bool
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    entered, release = Event(), Event()
    try:
        scope, project, _ = _seed(holder)
        graph = make_graph("gr_apply_guard", "apply-guard")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        app = _application(holder, scope.scope.tenant, tmp_path)
        graphs = GraphStore(scoped_connections(waiter, scope.scope.tenant))
        entries = DocumentEntries(
            scopes=(DeclaredScope("build", "", ScopeEdit(), project.slug),),
            routines=(DeclaredRoutine("build", graph.name, "build", (), None, (), RoutineEdit(), project.slug),),
        )
        original_enabled = GraphStore.enabled_names

        def pause_after_graph_read(self: GraphStore, names: Any) -> set[str]:
            result = original_enabled(self, names)
            entered.set()
            assert release.wait(8), "apply guard was not released"
            return result

        monkeypatch.setattr(GraphStore, "enabled_names", pause_after_graph_read)
        # Bypass admission only for the competing test writer, exposing the config key rather than the tenant row.
        admission = ScopedConnections._lock_open_tenant

        def test_admission(self: ScopedConnections, conn: sa.Connection) -> None:
            if conn.engine is not waiter:
                admission(self, conn)

        monkeypatch.setattr(ScopedConnections, "_lock_open_tenant", test_admission)
        if omit_lock:
            monkeypatch.setattr(graph_module, "lock_config_authoring", lambda conn: None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            applied = pool.submit(app.apply, entries, actor="test", dry_run=dry_run)
            try:
                assert entered.wait(8), "apply did not reach its graph guard"
                retirement = pool.submit(graphs.record_lifecycle, graph.graph_id, retired=True, at=_AT, by="test")
                if omit_lock:
                    retirement.result(timeout=5)
                    assert graphs.is_retired(graph.graph_id)
                else:
                    _wait_for_key(observer, retirement)
                    assert not graphs.is_retired(graph.graph_id)
            finally:
                release.set()
            result = applied.result(timeout=10)
            retirement.result(timeout=10)
            assert any(outcome.kind.value == "routine" and outcome.op is ChangeOp.CREATE for outcome in result.outcomes)
        assert graphs.is_retired(graph.graph_id)
    finally:
        release.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()
