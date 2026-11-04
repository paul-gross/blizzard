"""``PermitStore`` over a migrated sqlite-on-disk store (component tier)."""

# exemplar: bzh:tenant-scoped-store, bzh:bulk-reconstitution, bzh:store-refuses-stale. The revisions sit outside the
# hub's chain, so ``_migrated`` applies them over the real chain at their parent; production migrates to ``head``.

from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Engine

from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.tenancy import TenantId
from exemplar.foundation.permits import PermitState
from exemplar.hub.domain.permits.model import Permit, PermitAlreadyRequested, PermitChanged
from exemplar.hub.store.internal.permit_store import PermitStore
from exemplar.hub.store.schema import permit_facts
from tests.support import (
    add_tenant,
    count_queries,
    default_project,
    default_tenant,
    migrate_to,
    scoped_connections,
    seed_project,
)

_PERMITS = importlib.import_module("exemplar.hub.store.migrations.versions.20261015_0900_permits")
_PERMIT_REVISION = importlib.import_module("exemplar.hub.store.migrations.versions.20261103_0900_permit_revision")
_T0 = datetime(2026, 3, 1, tzinfo=UTC)


def _migrated(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, _PERMITS.down_revision)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        _PERMITS.upgrade()
        _PERMIT_REVISION.upgrade()
    return engine


def _project_of(engine: Engine, tenant: TenantId) -> ProjectId:
    return ProjectId(default_project(engine, tenant))


def _permit(n: int, project: ProjectId, *, holder: str = "ada", at: datetime | None = None) -> Permit:
    return Permit.request(
        f"pm_{n:03d}",
        project_id=project,
        holder=holder,
        capability=f"cap-{n}",
        by="op",
        at=at if at is not None else _T0 + timedelta(minutes=n),
    )


def _drain(store: PermitStore, *, project: ProjectId, holder: str, limit: int) -> list[str]:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = store.list_page(project=project, holder=holder, limit=limit, cursor=cursor)
        seen.extend(p.permit_id for p in page.permits)
        if page.next_cursor is None:
            return seen
        cursor = page.next_cursor


class _Abandoned(Exception):
    """Raised inside a unit to roll it back."""


def _fact_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(permit_facts)).scalar_one()


def _store(engine: Engine, tenant: TenantId) -> PermitStore:
    return PermitStore(scoped_connections(engine, tenant))


def test_a_tenant_reads_only_its_own_permits(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    first = default_tenant(engine)
    second = add_tenant(engine, "other")
    _store(engine, first).create(_permit(1, _project_of(engine, first)))
    _store(engine, second).create(_permit(2, _project_of(engine, second)))

    assert _store(engine, first).get("pm_002") is None
    page = _store(engine, second).list_page(project=None, holder=None, limit=10, cursor=None)
    assert [p.permit_id for p in page.permits] == ["pm_002"]


def test_a_duplicate_request_raises_already_requested(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    store.create(_permit(1, _project_of(engine, tenant)))
    duplicate = Permit.request(
        "pm_dup", project_id=_project_of(engine, tenant), holder="ada", capability="cap-1", by="op", at=_T0
    )

    with pytest.raises(PermitAlreadyRequested):
        store.create(duplicate)

    assert store.get("pm_dup") is None


def test_a_fact_at_the_revision_read_lands_as_the_newest_and_moves_the_revision(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    store.create(_permit(1, _project_of(engine, tenant)))
    read = store.get("pm_001")
    assert read is not None
    fact = read.grant(by="op", at=_T0 + timedelta(days=1))

    store.record_fact(fact, expected=read.revision)

    assert store.get("pm_001") == read.recorded(fact)


def test_a_fact_written_in_a_unit_rolls_back_with_it(tmp_path: Path) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    connections = scoped_connections(engine, tenant)
    store = PermitStore(connections)
    store.create(_permit(1, _project_of(engine, tenant)))
    read = store.get("pm_001")
    assert read is not None

    with pytest.raises(_Abandoned), HubTransactions(connections).begin() as transaction:
        store.record_fact(read.grant(by="op", at=_T0), expected=read.revision, transaction=transaction)
        raise _Abandoned

    assert store.get("pm_001") == read
    assert _fact_count(engine) == 0


def test_a_fact_decided_on_a_read_another_fact_moved_raises_permit_changed_and_lands_nothing(tmp_path: Path) -> None:
    """Two operators read the requested permit; one grants it, and the other's revoke from that same read is refused."""
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    store.create(_permit(1, _project_of(engine, tenant)))
    read = store.get("pm_001")
    assert read is not None
    store.record_fact(read.grant(by="op", at=_T0 + timedelta(days=1)), expected=read.revision)

    with pytest.raises(PermitChanged):
        store.record_fact(read.revoke(note="left", by="other", at=_T0 + timedelta(days=2)), expected=read.revision)

    after = store.get("pm_001")
    assert after is not None
    assert (after.state(), after.revision) == (PermitState.GRANTED, read.revision + 1)
    assert _fact_count(engine) == 1


def test_list_page_walks_one_holder_in_one_project_newest_first_ties_by_id_descending(tmp_path: Path) -> None:
    """Three permits share one ``requested_at`` across a page boundary; another project's and another holder's
    permits are left out."""
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    other = seed_project(engine, "other", tenant=tenant)
    tied = _T0 + timedelta(days=1)
    for permit in (
        _permit(1, project),
        _permit(2, project, at=tied),
        _permit(3, project, at=tied),
        _permit(4, project, at=tied),
        _permit(5, project, at=tied, holder="grace"),
        _permit(6, other, at=tied),
    ):
        store.create(permit)

    assert _drain(store, project=project, holder="ada", limit=2) == ["pm_004", "pm_003", "pm_002", "pm_001"]


@pytest.mark.parametrize("size", [3, 30])
def test_list_page_issues_the_same_statements_at_any_size(tmp_path: Path, size: int) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    for n in range(size):
        store.create(_permit(n, project))

    assert count_queries(engine, lambda: store.list_page(project=project, holder=None, limit=50, cursor=None)) == 2


@pytest.mark.parametrize("size", [3, 30])
def test_get_many_issues_the_same_statements_at_any_size(tmp_path: Path, size: int) -> None:
    engine = _migrated(tmp_path)
    tenant = default_tenant(engine)
    store = _store(engine, tenant)
    project = _project_of(engine, tenant)
    for n in range(size):
        store.create(_permit(n, project))

    assert count_queries(engine, lambda: store.get_many([f"pm_{n:03d}" for n in range(size)])) == 2
