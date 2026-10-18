"""The scoped store seam (``src/blizzard/hub/store/scoped.py``): every builder shape confines a statement to
the scope's tenant, the insert stamps the key, an insert into a project-keyed table naming no project and an
execute-time ``tenant_id`` are refused, a write on a closed tenant is refused inside its transaction, and a global table passes through untouched.

The seam is exercised over synthetic tables that carry ``tenant_id``, each statement run against
rows of two tenants so a missing or misplaced predicate shows up as a wrong row, not just as SQL."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Column, Engine, Integer, MetaData, String, Table
from structlog import get_logger

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections, HubStoreError, HubStoreErrorFactory
from blizzard.hub.store.scoped import (
    ProjectKeyMissing,
    ScopedConnections,
    TenantKeySupplied,
    UnscopedConnection,
    is_tenant_owned,
    statements_of,
)
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantClosed, TenantId, UnknownTenant
from tests.support import close_tenant

pytestmark = pytest.mark.component

TEN_A = TenantId("ten_A")
TEN_B = TenantId("ten_B")
CREATED_AT = datetime(2026, 1, 1, tzinfo=UTC)

md = MetaData()
chunks = Table(
    "chunks",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),  # runner-minted: two tenants may share one
    Column("state", String, nullable=False),
)
notes = Table(
    "notes",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("body", String, nullable=False),
)
facts = Table(
    "facts",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("key", String, nullable=False),
    Column("value", String, nullable=False),
)
catalog = Table(
    "catalog",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("value", String, nullable=False),
)
# Named on GLOBAL_TABLES: carries tenant_id as data, yet is read hub-wide.
memberships = Table(
    "membership_facts",
    md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=True),
    Column("user_id", String, nullable=True),
)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine_from_url(f"sqlite:///{tmp_path / 'scoped.db'}")
    md.create_all(eng)
    s.tenants.create(eng)
    s.tenant_deletions.create(eng)
    with eng.begin() as conn:
        conn.execute(
            sa.insert(s.tenants),
            [
                {"tenant_id": tenant, "name": tenant, "created_at": CREATED_AT, "created_by": "usr_test"}
                for tenant in (TEN_A, TEN_B)
            ],
        )
        conn.execute(
            sa.insert(chunks),
            [
                {"tenant_id": TEN_A, "chunk_id": "c1", "state": "open"},
                {"tenant_id": TEN_A, "chunk_id": "c2", "state": "open"},
                {"tenant_id": TEN_B, "chunk_id": "c1", "state": "open"},
                {"tenant_id": TEN_B, "chunk_id": "c2", "state": "open"},
            ],
        )
        conn.execute(
            sa.insert(notes),
            [
                {"tenant_id": TEN_A, "chunk_id": "c1", "body": "a-c1"},
                {"tenant_id": TEN_B, "chunk_id": "c1", "body": "b-c1"},
                {"tenant_id": TEN_B, "chunk_id": "c2", "body": "b-c2"},
            ],
        )
        conn.execute(
            sa.insert(facts),
            [
                {"tenant_id": TEN_A, "key": "k", "value": "a-old"},
                {"tenant_id": TEN_A, "key": "k", "value": "a-new"},
                {"tenant_id": TEN_B, "key": "k", "value": "b-newest"},
            ],
        )
    yield eng
    eng.dispose()


@pytest.fixture
def hub(engine: Engine) -> HubStoreConnections:
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))


@pytest.fixture
def scoped(hub: HubStoreConnections) -> ScopedConnections:
    return hub.scoped(StoreScope(TEN_A))


def _rows(scoped: ScopedConnections, stmt: Any) -> list[tuple[Any, ...]]:
    with scoped.read("test") as conn:
        return [tuple(r) for r in conn.execute(stmt)]


def _sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# --- The scope and the seam's shape ------------------------------------------------------


def test_the_scope_is_the_tenant_and_nothing_narrower() -> None:
    assert [f.name for f in dataclasses.fields(StoreScope)] == ["tenant"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        StoreScope(TEN_A).tenant = TEN_B  # type: ignore[misc]


def test_scoped_connections_are_hub_store_connections_bound_to_the_scope(
    hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    assert isinstance(scoped, HubStoreConnections)
    assert scoped.scope == StoreScope(TEN_A)


def test_scoped_connections_refuse_to_be_moved_to_another_tenant(scoped: ScopedConnections) -> None:
    assert scoped.scoped(StoreScope(TEN_A)) is scoped
    with pytest.raises(ValueError, match="cannot be rescoped"):
        scoped.scoped(StoreScope(TEN_B))


def test_a_driver_fault_through_the_scoped_seam_is_wrapped(scoped: ScopedConnections) -> None:
    with pytest.raises(HubStoreError, match="scoped probe"), scoped.write("scoped probe") as conn:
        conn.execute(sa.text("SELECT * FROM no_such_table"))


def test_tenant_owned_means_a_tenant_id_column_off_the_global_list() -> None:
    assert is_tenant_owned(chunks)
    assert is_tenant_owned(chunks.alias("c"))
    assert is_tenant_owned(chunks.alias("c").alias("cc"))
    assert not is_tenant_owned(catalog)
    assert not is_tenant_owned(memberships)
    assert not is_tenant_owned(sa.join(chunks, notes, chunks.c.chunk_id == notes.c.chunk_id))
    assert not is_tenant_owned(sa.select(chunks).subquery())


# --- select, aliases, joins ---------------------------------------------------------------


def test_select_reads_only_the_scope_tenants_rows(scoped: ScopedConnections) -> None:
    rows = _rows(scoped, scoped.select(chunks, chunks.c.tenant_id, chunks.c.chunk_id).order_by(chunks.c.chunk_id))
    assert rows == [(TEN_A, "c1"), (TEN_A, "c2")]


def test_select_without_columns_reads_every_column(scoped: ScopedConnections) -> None:
    rows = _rows(scoped, scoped.select(notes))
    assert [r[1:] for r in rows] == [(TEN_A, "c1", "a-c1")]


def test_select_of_an_alias_scopes_the_alias(scoped: ScopedConnections) -> None:
    newer = facts.alias("newer")
    stmt = scoped.select(newer, newer.c.value).order_by(newer.c.id)
    assert "newer.tenant_id = 'ten_A'" in _sql(stmt)
    assert _rows(scoped, stmt) == [("a-old",), ("a-new",)]


def test_an_inner_join_reads_only_the_tenants_rows_on_both_sides(scoped: ScopedConnections) -> None:
    joined = scoped.scope_join(chunks, notes, chunks.c.chunk_id == notes.c.chunk_id)
    rows = _rows(scoped, scoped.select(joined, chunks.c.tenant_id, notes.c.tenant_id, notes.c.body))
    assert rows == [(TEN_A, TEN_A, "a-c1")]


def test_an_outer_join_puts_the_right_sides_predicate_in_on_and_keeps_unmatched_rows(
    scoped: ScopedConnections,
) -> None:
    joined = scoped.scope_join(chunks, notes, chunks.c.chunk_id == notes.c.chunk_id, isouter=True)
    stmt = scoped.select(joined, chunks.c.chunk_id, notes.c.body).order_by(chunks.c.chunk_id)
    on_clause, where_clause = _sql(stmt).split("WHERE")
    assert "notes.tenant_id = 'ten_A'" in on_clause
    assert "notes.tenant_id" not in where_clause
    assert "chunks.tenant_id = 'ten_A'" in where_clause
    # c2 has a note only in the other tenant: it stays, unmatched, rather than vanishing or leaking.
    assert _rows(scoped, stmt) == [("c1", "a-c1"), ("c2", None)]


def test_an_outer_join_onto_an_alias_scopes_the_alias_in_on(scoped: ScopedConnections) -> None:
    other = notes.alias("other")
    joined = scoped.scope_join(chunks, other, chunks.c.chunk_id == other.c.chunk_id, isouter=True)
    stmt = scoped.select(joined, chunks.c.chunk_id, other.c.body).order_by(chunks.c.chunk_id)
    assert _rows(scoped, stmt) == [("c1", "a-c1"), ("c2", None)]


def test_a_chained_join_scopes_every_table_it_names(scoped: ScopedConnections) -> None:
    inner = scoped.scope_join(chunks, notes, chunks.c.chunk_id == notes.c.chunk_id)
    newest = facts.alias("f")
    joined = scoped.scope_join(inner, newest, newest.c.key == "k", isouter=True)
    stmt = scoped.select(joined, notes.c.body, newest.c.value).order_by(newest.c.id)
    assert _rows(scoped, stmt) == [("a-c1", "a-old"), ("a-c1", "a-new")]


# --- EXISTS, FROM-less selects, UNION, grouped subqueries --------------------------------


def test_a_correlated_exists_and_not_exists_count_only_the_tenants_rows(scoped: ScopedConnections) -> None:
    has_note = scoped.exists(notes, notes.c.chunk_id == chunks.c.chunk_id)
    with_note = scoped.select(chunks, chunks.c.chunk_id).where(has_note)
    without_note = scoped.select(chunks, chunks.c.chunk_id).where(~has_note)
    assert _rows(scoped, with_note) == [("c1",)]
    # The other tenant's note on c2 does not count.
    assert _rows(scoped, without_note) == [("c2",)]


def test_a_correlated_exists_over_an_alias_is_scoped(scoped: ScopedConnections) -> None:
    newer = facts.alias("newer")
    superseded = scoped.exists(newer, newer.c.key == facts.c.key, newer.c.id > facts.c.id)
    stmt = scoped.select(facts, facts.c.value).where(~superseded)
    # The other tenant's later fact does not supersede this tenant's newest one.
    assert _rows(scoped, stmt) == [("a-new",)]


def test_a_from_less_select_over_scoped_subqueries(scoped: ScopedConnections) -> None:
    probe = sa.select(
        scoped.exists(notes, notes.c.chunk_id == "c2"),
        scoped.select(chunks, sa.func.count()).scalar_subquery(),
        scoped.select(notes, sa.func.count()).scalar_subquery(),
    )
    assert _rows(scoped, probe) == [(False, 2, 1)]


def test_union_arms_are_each_scoped(scoped: ScopedConnections) -> None:
    ids = sa.union(
        scoped.select(chunks, chunks.c.chunk_id.label("ref")),
        scoped.select(notes, notes.c.chunk_id.label("ref")),
        scoped.select(facts, facts.c.key.label("ref")),
    )
    assert sorted(_rows(scoped, ids)) == [("c1",), ("c2",), ("k",)]
    tenants = sa.union_all(
        scoped.select(chunks, chunks.c.tenant_id),
        scoped.select(notes, notes.c.tenant_id),
    )
    assert {r[0] for r in _rows(scoped, tenants)} == {TEN_A}


def test_a_newest_fact_grouped_subquery_is_scoped_inside(scoped: ScopedConnections) -> None:
    newest = scoped.select(facts, sa.func.max(facts.c.id).label("id")).group_by(facts.c.key).subquery()
    stmt = scoped.select(facts, facts.c.value).join(newest, facts.c.id == newest.c.id)
    assert _rows(scoped, stmt) == [("a-new",)]
    # Scoping only the outer read loses the row: the hub-wide max belongs to the other tenant.
    unscoped_group = sa.select(sa.func.max(facts.c.id).label("id")).group_by(facts.c.key).subquery()
    assert (
        _rows(scoped, scoped.select(facts, facts.c.value).join(unscoped_group, facts.c.id == unscoped_group.c.id)) == []
    )


# --- insert, update, delete ---------------------------------------------------------------


def _all_notes(hub: HubStoreConnections) -> list[tuple[Any, ...]]:
    with hub.read("test") as conn:
        return [tuple(r) for r in conn.execute(sa.select(notes.c.tenant_id, notes.c.body).order_by(notes.c.id))]


def test_insert_stamps_the_tenant_into_every_row(hub: HubStoreConnections, scoped: ScopedConnections) -> None:
    with scoped.write("test") as conn:
        conn.execute(scoped.insert(notes).values(chunk_id="c9", body="one"))
        conn.execute(scoped.insert(notes), [{"chunk_id": "c9", "body": "two"}, {"chunk_id": "c9", "body": "three"}])
    assert _all_notes(hub)[-3:] == [(TEN_A, "one"), (TEN_A, "two"), (TEN_A, "three")]


def test_a_multi_row_insert_stamps_the_tenant_into_each_row(
    hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    rows = [{"chunk_id": "c9", "body": "one"}, {notes.c.chunk_id: "c9", notes.c.body: "two"}]
    with scoped.write("test") as conn:
        conn.execute(scoped.insert_rows(notes, rows))
    assert _all_notes(hub)[-2:] == [(TEN_A, "one"), (TEN_A, "two")]


def test_an_insert_from_select_stamps_the_tenant_into_each_row(
    hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    # Copies the tenant's open chunks into notes; the other tenant's chunks are not read.
    source = scoped.select(chunks, chunks.c.chunk_id, chunks.c.state).where(chunks.c.state == "open")
    with scoped.write("test") as conn:
        conn.execute(scoped.insert_from_select(notes, ["chunk_id", "body"], source))
    assert _all_notes(hub)[-2:] == [(TEN_A, "open"), (TEN_A, "open")]
    with hub.read("test") as conn:
        copied = conn.execute(sa.select(notes.c.chunk_id).where(notes.c.id > 3).order_by(notes.c.id)).scalars()
        assert list(copied) == ["c1", "c2"]


@pytest.mark.parametrize(
    "build",
    [
        lambda b: b.insert_rows(notes, [{"chunk_id": "c9", "body": "x"}, {"chunk_id": "c9", "tenant_id": TEN_B}]),
        lambda b: b.insert_rows(notes, [{notes.c.tenant_id: TEN_B, "chunk_id": "c9", "body": "x"}]),
        lambda b: b.insert_from_select(
            notes, ["chunk_id", "tenant_id"], sa.select(sa.literal("c9"), sa.literal(TEN_B))
        ),
    ],
    ids=["row-name", "row-column", "select-names"],
)
def test_a_tenant_id_supplied_to_a_multi_row_builder_is_refused(scoped: ScopedConnections, build: Any) -> None:
    with pytest.raises(TenantKeySupplied, match="notes"):
        build(scoped)


def test_a_multi_row_insert_needs_a_row(scoped: ScopedConnections) -> None:
    with pytest.raises(ValueError, match="no rows"):
        scoped.insert_rows(notes, [])


@pytest.mark.parametrize(
    "params",
    [
        [{"chunk_id": "c9", "body": "x"}, {"chunk_id": "c9", "body": "y", "tenant_id": TEN_B}],
        {"chunk_id": "c9", "body": "x", "tenant_id": TEN_B},
    ],
    ids=["executemany", "single"],
)
def test_an_execute_time_tenant_id_on_an_insert_is_refused(
    hub: HubStoreConnections, scoped: ScopedConnections, params: Any
) -> None:
    before = _all_notes(hub)
    with pytest.raises(TenantKeySupplied, match="notes"), scoped.write("test") as conn:
        conn.execute(scoped.insert(notes), params)
    assert _all_notes(hub) == before


def test_an_execute_time_tenant_id_on_an_update_is_refused(scoped: ScopedConnections) -> None:
    # An executemany update's parameters named after a column set that column: this one would
    # move the row into the other tenant.
    stmt = scoped.update(notes).where(notes.c.chunk_id == sa.bindparam("ref"))
    with pytest.raises(TenantKeySupplied), scoped.write("test") as conn:
        conn.execute(stmt, [{"ref": "c1", "body": "moved", "tenant_id": TEN_B}])


def test_update_and_delete_touch_only_the_tenants_rows(hub: HubStoreConnections, scoped: ScopedConnections) -> None:
    with scoped.write("test") as conn:
        conn.execute(scoped.update(notes).where(notes.c.chunk_id == "c1").values(body="edited"))
        conn.execute(scoped.delete(chunks).where(chunks.c.chunk_id == "c2"))
    assert _all_notes(hub) == [(TEN_A, "edited"), (TEN_B, "b-c1"), (TEN_B, "b-c2")]
    with hub.read("test") as conn:
        left = conn.execute(sa.select(chunks.c.tenant_id, chunks.c.chunk_id).order_by(chunks.c.id)).all()
    assert [tuple(r) for r in left] == [(TEN_A, "c1"), (TEN_B, "c1"), (TEN_B, "c2")]


def test_a_global_table_passes_through_every_builder_untouched(
    hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    for table in (catalog, memberships):
        assert _sql(scoped.select(table)) == _sql(sa.select(table))
        assert _sql(scoped.insert(table)) == _sql(sa.insert(table))
        assert _sql(scoped.update(table).values(id=1)) == _sql(sa.update(table).values(id=1))
        assert _sql(scoped.delete(table)) == _sql(sa.delete(table))
        rows = [{"id": 7}, {"id": 8}]
        assert _sql(scoped.insert_rows(table, rows)) == _sql(sa.insert(table).values(rows))
        source = sa.select(sa.literal(7))
        assert _sql(scoped.insert_from_select(table, ["id"], source)) == _sql(
            sa.insert(table).from_select(["id"], source)
        )
    assert _sql(scoped.insert_rows(memberships, [{"tenant_id": TEN_B}])) == _sql(
        sa.insert(memberships).values([{"tenant_id": TEN_B}])
    )
    with scoped.write("test") as conn:
        conn.execute(scoped.insert(catalog).values(value="v"))
        conn.execute(scoped.insert(memberships), [{"tenant_id": TEN_B}])
    with hub.read("test") as conn:
        assert conn.execute(sa.select(memberships.c.tenant_id)).scalar_one() == TEN_B
        assert conn.execute(sa.select(catalog.c.value)).scalar_one() == "v"


# --- Builders reached from a bare Connection ----------------------------------------------


def test_a_scoped_connection_yields_the_same_builders(scoped: ScopedConnections) -> None:
    with scoped.read("test") as conn:
        assert statements_of(conn).scope == scoped.scope
        assert [tuple(r) for r in conn.execute(statements_of(conn).select(notes, notes.c.body))] == [("a-c1",)]
    with scoped.write("test") as conn:
        assert statements_of(conn).scope == scoped.scope


def test_an_unscoped_connection_yields_no_builders(hub: HubStoreConnections, scoped: ScopedConnections) -> None:
    with scoped.read("test"):
        pass
    # The scope rode the scoped block's Connection, never the pooled driver connection behind it.
    with hub.read("test") as conn, pytest.raises(UnscopedConnection):
        statements_of(conn)
    with hub.write("test") as conn:
        with pytest.raises(UnscopedConnection):
            statements_of(conn)
        # The execute-time refusal is the scoped seam's alone.
        conn.execute(sa.insert(notes), [{"tenant_id": TEN_B, "chunk_id": "c9", "body": "raw"}])


# --- A closed tenant ---------------------------------------------------------------------


def test_a_write_on_a_closed_tenant_is_refused_inside_its_transaction(
    engine: Engine, hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    before = _all_notes(hub)
    close_tenant(engine, TEN_A)
    reached = False
    with pytest.raises(TenantClosed) as refused, scoped.write("test") as conn:
        reached = True
        conn.execute(scoped.insert(notes).values(chunk_id="c9", body="late"))
    assert refused.value.tenant == TEN_A
    assert not reached
    assert _all_notes(hub) == before
    # Reads of a closed tenant still run; another tenant's writes are unaffected.
    assert _rows(scoped, scoped.select(notes, notes.c.body)) == [("a-c1",)]
    other = hub.scoped(StoreScope(TEN_B))
    with other.write("test") as conn:
        conn.execute(other.insert(notes).values(chunk_id="c9", body="b-later"))
    assert _all_notes(hub)[-1] == (TEN_B, "b-later")


def test_a_write_on_a_drained_tenant_is_refused(
    engine: Engine, hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    # A finished teardown leaves the deletion fact and no tenant row.
    close_tenant(engine, TEN_A)
    with hub.write("test") as conn:
        conn.execute(sa.delete(s.tenants).where(s.tenants.c.tenant_id == TEN_A))
    with pytest.raises(TenantClosed), scoped.write("test"):
        pass


def test_a_write_on_an_unknown_tenant_is_refused(hub: HubStoreConnections) -> None:
    nobody = hub.scoped(StoreScope(TenantId("ten_NOBODY")))
    with pytest.raises(UnknownTenant) as refused, nobody.write("test"):
        pass
    assert refused.value.tenant == "ten_NOBODY"


def test_a_close_cannot_commit_while_an_open_write_is_in_flight(
    engine: Engine, hub: HubStoreConnections, scoped: ScopedConnections
) -> None:
    # A close committed after the writer opened would let the writer's rows land behind the
    # drain. The writer's opening statement holds the store's write lock, so a close from
    # another connection waits for the writer's commit — here, with no busy wait, it fails.
    closer = sqlite3.connect(str(engine.url.database), timeout=0, isolation_level=None)
    close = (
        "INSERT INTO tenant_deletions (tenant_id, name, closed_at, deleted_by) VALUES (?, ?, '2026-01-01', 'usr_test')"
    )
    try:
        with scoped.write("test") as conn:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                closer.execute(close, (TEN_A, TEN_A))
            conn.execute(scoped.insert(notes).values(chunk_id="c9", body="in-flight"))
        closer.execute(close, (TEN_A, TEN_A))
    finally:
        closer.close()
    # The writer's row committed before the close, so a drain that follows the close sees it.
    assert _all_notes(hub)[-1] == (TEN_A, "in-flight")
    with pytest.raises(TenantClosed), scoped.write("test"):
        pass


# --- The project key, over the hub's own project-keyed tables ---

TEN_ONE = TenantId("ten_one")
TEN_SEVERAL = TenantId("ten_several")


@pytest.fixture
def projected(tmp_path: Path) -> Iterator[HubStoreConnections]:
    """Two tenants over the hub's ``scopes`` and change log: one holding a project, one holding two."""
    eng = create_engine_from_url(f"sqlite:///{tmp_path / 'projected.db'}")
    for table in (s.tenants, s.tenant_deletions, s.projects, s.scopes, s.config_changes):
        table.create(eng)
    with eng.begin() as conn:
        conn.execute(
            sa.insert(s.tenants),
            [
                {"tenant_id": tenant, "name": tenant, "created_at": CREATED_AT, "created_by": "usr_test"}
                for tenant in (TEN_ONE, TEN_SEVERAL)
            ],
        )
        conn.execute(
            sa.insert(s.projects),
            [
                {
                    "project_id": project,
                    "tenant_id": tenant,
                    "slug": slug,
                    "name": slug,
                    "description": "",
                    "color": "red",
                    "revision": 1,
                    "created_at": CREATED_AT,
                }
                for project, tenant, slug in (
                    ("proj_one", TEN_ONE, "default"),
                    ("proj_first", TEN_SEVERAL, "default"),
                    ("proj_second", TEN_SEVERAL, "second"),
                )
            ],
        )
    yield HubStoreConnections(eng, HubStoreErrorFactory(get_logger("test")))
    eng.dispose()


def _scope(slug: str, **values: Any) -> dict[str, Any]:
    return {"scope_id": f"scp_{slug}", "slug": slug, "description": "", "created_at": CREATED_AT, **values}


def _scope_projects(hub: HubStoreConnections) -> dict[str, str]:
    with hub.read("scope_projects") as conn:
        rows = conn.execute(sa.select(s.scopes.c.slug, s.scopes.c.project_id).execution_options(**_HUB_WIDE))
        return {row.slug: row.project_id for row in rows}


_HUB_WIDE = {HUB_SCOPED_OPTION: True}


_SCOPE_NAMES = ["scope_id", "slug", "description", "created_at"]


def _selected_scope(*project: str) -> sa.Select[Any]:
    return sa.select(
        sa.literal("scp_selected"),
        sa.literal("selected"),
        sa.literal(""),
        sa.literal(CREATED_AT, s.scopes.c.created_at.type),
        *(sa.literal(p) for p in project),
    )


@pytest.mark.parametrize(
    "params",
    [None, [{**_scope("many-a"), "project_id": "proj_one"}, _scope("many-b")]],
    ids=["values", "executemany-row"],
)
def test_insert_naming_no_project_is_refused_at_execute_even_for_a_tenant_with_one_project(
    projected: HubStoreConnections, params: list[dict[str, Any]] | None
) -> None:
    conns = projected.scoped(StoreScope(TEN_ONE))
    with pytest.raises(ProjectKeyMissing) as refused, conns.write("insert", expect=(ProjectKeyMissing,)) as conn:
        if params is None:
            conn.execute(conns.insert(s.scopes).values(_scope("orphan")))
        else:
            conn.execute(conns.insert(s.scopes), params)

    assert refused.value.table == "scopes"
    assert _scope_projects(projected) == {}


def test_insert_rows_with_a_row_naming_no_project_is_refused_as_it_builds(projected: HubStoreConnections) -> None:
    conns = projected.scoped(StoreScope(TEN_ONE))
    rows = [_scope("named", project_id="proj_one"), _scope("orphan")]
    with pytest.raises(ProjectKeyMissing) as refused:
        conns.insert_rows(s.scopes, rows)

    assert refused.value.table == "scopes"


def test_insert_from_select_naming_no_project_is_refused_as_it_builds(projected: HubStoreConnections) -> None:
    conns = projected.scoped(StoreScope(TEN_ONE))
    with pytest.raises(ProjectKeyMissing) as refused:
        conns.insert_from_select(s.scopes, _SCOPE_NAMES, _selected_scope())

    assert refused.value.table == "scopes"


def test_each_builder_writes_the_project_its_caller_names(projected: HubStoreConnections) -> None:
    conns = projected.scoped(StoreScope(TEN_SEVERAL))
    with conns.write("insert") as conn:
        conn.execute(conns.insert(s.scopes).values(_scope("values", project_id="proj_second")))
        conn.execute(conns.insert(s.scopes), [_scope("params", project_id="proj_first")])
        conn.execute(conns.insert_rows(s.scopes, [_scope("rows", project_id="proj_first")]))
        names = [*_SCOPE_NAMES, "project_id"]
        conn.execute(conns.insert_from_select(s.scopes, names, _selected_scope("proj_second")))

    assert _scope_projects(projected) == {
        "values": "proj_second",
        "params": "proj_first",
        "rows": "proj_first",
        "selected": "proj_second",
    }


def test_the_change_log_takes_no_project_key(projected: HubStoreConnections) -> None:
    conns = projected.scoped(StoreScope(TEN_ONE))
    row = {
        "recorded_at": CREATED_AT,
        "actor": "usr_test",
        "door": "api",
        "record_kind": "work_source",
        "record_key": "github",
        "revision": 1,
        "op": "create",
        "diff": "[]",
    }
    with conns.write("insert") as conn:
        conn.execute(conns.insert(s.config_changes).values(row))
    with conns.read("changes") as conn:
        assert conn.execute(conns.select(s.config_changes, s.config_changes.c.project_id)).scalars().all() == [None]
