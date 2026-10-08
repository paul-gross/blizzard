from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.hub.store import schema
from blizzard.hub.store.scoped import ScopedStatements
from blizzard.hub.store.tenant_guard import (
    GUARD_ENVIRONMENT_VARIABLE,
    TenantStatementGuard,
    UnscopedTenantStatement,
    guard_hub_statement,
    install_tenant_guard,
)
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId

pytestmark = pytest.mark.unit

_T = "ten_a"

_md = sa.MetaData()
notes = sa.Table(
    "notes",
    _md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("tenant_id", sa.String),
    sa.Column("owner_id", sa.Integer),
    sa.Column("body", sa.String),
)
owners = sa.Table(
    "owners",
    _md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("tenant_id", sa.String, nullable=False),
)
# Global by name, though it carries ``tenant_id`` as data.
tenants = sa.Table("tenants", _md, sa.Column("tenant_id", sa.String, primary_key=True))
plain = sa.Table("plain", _md, sa.Column("id", sa.Integer, primary_key=True), sa.Column("label", sa.String))

_other_md = sa.MetaData()
foreign_notes = sa.Table(
    "foreign_notes",
    _other_md,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("tenant_id", sa.String),
)

Statement = Any
Params = Mapping[str, Any] | Sequence[Mapping[str, Any]] | None


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = sa.create_engine("sqlite://", poolclass=sa.StaticPool)
    _md.create_all(engine)
    _other_md.create_all(engine)
    event.listen(engine, "before_execute", TenantStatementGuard(_md).before_execute)
    yield engine
    engine.dispose()


def _run(engine: Engine, statement: Statement, params: Params = None) -> None:
    with engine.begin() as conn:
        conn.execute(statement, params)


def _newest_note_per_owner(*, pinned: bool) -> sa.Select[Any]:
    """The newest-fact read: each owner's newest note, through a grouped subquery joined back to its own table."""
    inner = sa.select(notes.c.owner_id, sa.func.max(notes.c.id).label("id")).group_by(notes.c.owner_id)
    newest = (inner.where(notes.c.tenant_id == _T) if pinned else inner).subquery()
    return sa.select(notes).join(newest, newest.c.id == notes.c.id).where(notes.c.tenant_id == _T)


_aliased = notes.alias("n2")


def _newest_id_per_owner() -> sa.Select[Any]:
    """Each owner's newest note id, grouped over the whole table: nothing in it names a tenant."""
    return sa.select(sa.func.max(notes.c.id)).group_by(notes.c.owner_id)


def _owner_counts(*, pinned: bool) -> sa.Subquery:
    """Notes per owner — a grouped subquery an ``UPDATE … FROM`` or ``DELETE … USING`` reads."""
    counted = sa.select(notes.c.owner_id, sa.func.count().label("n")).group_by(notes.c.owner_id)
    return (counted.where(notes.c.tenant_id == _T) if pinned else counted).subquery()


def _update_from_owner_counts(*, pinned: bool) -> sa.Update:
    counts = _owner_counts(pinned=pinned)
    return (
        sa.update(notes)
        .where(notes.c.tenant_id == _T, notes.c.owner_id == counts.c.owner_id)
        .values(body=sa.cast(counts.c.n, sa.String))
    )


def _delete_using_owner_counts(*, pinned: bool) -> sa.Delete:
    counts = _owner_counts(pinned=pinned)
    return sa.delete(notes).where(notes.c.tenant_id == _T, notes.c.owner_id == counts.c.owner_id)


def _owner_lateral(*, pinned: bool) -> sa.Select[Any]:
    """A ``LATERAL`` read of each note's owner, correlated to the note beside it in ``FROM``."""
    owner = sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id)
    lateral = (owner.where(owners.c.tenant_id == _T) if pinned else owner).lateral("owner")
    return (
        sa.select(notes.c.id, lateral.c.id).select_from(notes).join(lateral, sa.true()).where(notes.c.tenant_id == _T)
    )


_UNSCOPED: list[Any] = [
    pytest.param(sa.select(notes), None, "select names tenant-owned table notes ", id="select-without-where"),
    pytest.param(
        sa.select(notes).where(notes.c.id == 1),
        None,
        "select names tenant-owned table notes ",
        id="select-where-names-no-tenant",
    ),
    pytest.param(
        sa.select(notes).where(sa.or_(notes.c.tenant_id == _T, notes.c.id == 1)),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equality-under-or",
    ),
    pytest.param(
        sa.select(notes).where(notes.c.tenant_id == notes.c.tenant_id),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equality-to-its-own-column",
    ),
    pytest.param(
        sa.select(notes).where(notes.c.tenant_id != _T),
        None,
        "select names tenant-owned table notes ",
        id="tenant-inequality",
    ),
    pytest.param(
        sa.select(notes.c.id).join_from(notes, owners, owners.c.id == notes.c.owner_id).where(notes.c.tenant_id == _T),
        None,
        "select names tenant-owned table owners ",
        id="joined-table-unpinned",
    ),
    pytest.param(
        sa.select(notes.c.id, _aliased.c.id).where(notes.c.tenant_id == _T, _aliased.c.id == notes.c.owner_id),
        None,
        "select names tenant-owned table notes (as n2) ",
        id="alias-unpinned-beside-pinned-base",
    ),
    pytest.param(
        sa.select(notes).where(
            notes.c.tenant_id == _T, sa.exists(sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id))
        ),
        None,
        "select names tenant-owned table owners ",
        id="correlated-exists-unpinned",
    ),
    pytest.param(
        sa.select(notes.c.id, sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id).scalar_subquery()).where(
            notes.c.tenant_id == _T
        ),
        None,
        "select names tenant-owned table owners ",
        id="scalar-subquery-unpinned",
    ),
    pytest.param(
        _newest_note_per_owner(pinned=False),
        None,
        "select names tenant-owned table notes ",
        id="grouped-subquery-in-from-does-not-correlate",
    ),
    pytest.param(
        sa.union(sa.select(notes.c.id).where(notes.c.tenant_id == _T), sa.select(notes.c.id)),
        None,
        "select names tenant-owned table notes ",
        id="union-arm-unpinned",
    ),
    pytest.param(
        sa.select(sa.func.count()).select_from(notes),
        None,
        "select names tenant-owned table notes ",
        id="count-over-select-from",
    ),
    pytest.param(
        sa.update(notes).where(notes.c.id == 1).values(body="x"),
        None,
        "update names tenant-owned table notes ",
        id="update",
    ),
    pytest.param(sa.delete(notes).where(notes.c.id == 1), None, "delete names tenant-owned table notes ", id="delete"),
    pytest.param(
        sa.update(notes).where(notes.c.tenant_id == _T, notes.c.owner_id == owners.c.id).values(body="x"),
        None,
        "update names tenant-owned table owners ",
        id="update-from-unpinned",
    ),
    pytest.param(
        sa.delete(notes).where(notes.c.tenant_id == _T, notes.c.id.in_(sa.select(owners.c.id).where(owners.c.id > 0))),
        None,
        "select names tenant-owned table owners ",
        id="delete-with-unpinned-subquery",
    ),
    pytest.param(
        sa.select(notes.c.body).where(notes.c.tenant_id == _T, notes.c.id.in_(_newest_id_per_owner())),
        None,
        "select names tenant-owned table notes ",
        id="in-subquery-over-its-own-table-does-not-correlate",
    ),
    pytest.param(
        sa.delete(notes).where(notes.c.tenant_id == _T, notes.c.id.not_in(_newest_id_per_owner())),
        None,
        "select names tenant-owned table notes ",
        id="delete-not-in-subquery-over-its-own-table",
    ),
    pytest.param(
        sa.select(notes.c.id, sa.select(sa.func.count()).select_from(notes).scalar_subquery()).where(
            notes.c.tenant_id == _T
        ),
        None,
        "select names tenant-owned table notes ",
        id="scalar-count-over-its-own-table-does-not-correlate",
    ),
    pytest.param(
        sa.select(notes).where(
            notes.c.tenant_id == _T,
            sa.exists(
                sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id, owners.c.tenant_id == _T).correlate(None)
            ),
        ),
        None,
        "select names tenant-owned table notes ",
        id="correlate-none-reads-the-outer-table-unscoped",
    ),
    pytest.param(
        sa.select(notes.c.id).where(
            notes.c.tenant_id == _T,
            notes.c.owner_id.in_(sa.select(sa.select(notes.c.owner_id).group_by(notes.c.owner_id).cte("c").c.owner_id)),
        ),
        None,
        "select names tenant-owned table notes ",
        id="cte-does-not-correlate",
    ),
    pytest.param(
        sa.select(notes.c.id).where(
            notes.c.tenant_id == _T,
            notes.c.owner_id.in_(sa.select(sa.select(notes.c.owner_id).subquery().c.owner_id)),
        ),
        None,
        "select names tenant-owned table notes ",
        id="derived-table-in-a-where-subquery-does-not-correlate",
    ),
    pytest.param(
        _update_from_owner_counts(pinned=False),
        None,
        "select names tenant-owned table notes ",
        id="update-from-unpinned-subquery",
    ),
    pytest.param(
        _delete_using_owner_counts(pinned=False),
        None,
        "select names tenant-owned table notes ",
        id="delete-using-unpinned-subquery",
    ),
    pytest.param(
        sa.select(notes).join(tenants, notes.c.tenant_id == tenants.c.tenant_id),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equated-to-a-global-table",
    ),
    pytest.param(
        sa.select(notes.c.id, owners.c.id).where(
            notes.c.tenant_id == owners.c.tenant_id, notes.c.owner_id == owners.c.id
        ),
        None,
        "select names tenant-owned table owners ",
        id="two-tables-equated-only-to-each-other",
    ),
    pytest.param(
        sa.select(notes.c.id, _aliased.c.id).where(notes.c.tenant_id == _aliased.c.tenant_id),
        None,
        "select names tenant-owned table notes (as n2) ",
        id="self-join-equated-only-to-itself",
    ),
    pytest.param(
        sa.select(notes).where(notes.c.tenant_id == notes.c.body),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equated-to-another-column",
    ),
    pytest.param(
        sa.select(notes).where(notes.c.tenant_id == sa.func.coalesce(notes.c.tenant_id, _T)),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equated-to-a-function-of-itself",
    ),
    pytest.param(
        sa.select(notes).where(notes.c.tenant_id == sa.literal_column("tenant_id")),
        None,
        "select names tenant-owned table notes ",
        id="tenant-equated-to-a-literal-column",
    ),
    pytest.param(
        sa.update(notes)
        .where(notes.c.tenant_id == owners.c.tenant_id, owners.c.id == notes.c.owner_id)
        .values(body="x"),
        None,
        "update names tenant-owned table notes ",
        id="update-equated-to-another-table",
    ),
    pytest.param(
        sa.select(tenants).where(sa.exists(sa.select(notes.c.id).where(notes.c.tenant_id == tenants.c.tenant_id))),
        None,
        "select names tenant-owned table notes ",
        id="correlated-to-a-global-table",
    ),
    pytest.param(
        sa.select(notes.c.id, owners.c.id).select_from(
            notes.outerjoin(
                owners, sa.and_(owners.c.id == notes.c.owner_id, notes.c.tenant_id == _T, owners.c.tenant_id == _T)
            )
        ),
        None,
        "select names tenant-owned table notes ",
        id="left-join-on-does-not-scope-its-preserved-side",
    ),
    pytest.param(
        sa.select(notes.c.id, owners.c.id).select_from(
            notes.outerjoin(
                owners,
                sa.and_(owners.c.id == notes.c.owner_id, notes.c.tenant_id == _T, owners.c.tenant_id == _T),
                full=True,
            )
        ),
        None,
        "select names tenant-owned table owners ",
        id="full-join-on-scopes-neither-side",
    ),
    pytest.param(_owner_lateral(pinned=False), None, "select names tenant-owned table owners ", id="lateral-unpinned"),
    pytest.param(sa.insert(notes).values(id=1, body="x"), None, "insert into tenant-owned table notes", id="insert"),
    pytest.param(
        sa.insert(notes), [{"id": 1}, {"id": 2}], "insert into tenant-owned table notes", id="insert-executemany"
    ),
    pytest.param(
        sa.insert(notes).values([{"id": 1, "body": "x"}, {"id": 2, "body": "y"}]),
        None,
        "insert into tenant-owned table notes",
        id="insert-multi-values",
    ),
    pytest.param(
        sa.insert(notes).from_select(["id"], sa.select(plain.c.id)),
        None,
        "insert into tenant-owned table notes",
        id="insert-from-select",
    ),
]


@pytest.mark.parametrize(("statement", "params", "names"), _UNSCOPED)
def test_an_unscoped_statement_is_refused_before_it_runs(
    engine: Engine, statement: Statement, params: Params, names: str
) -> None:
    with pytest.raises(UnscopedTenantStatement, match=re.escape(names)):
        _run(engine, statement, params)
    with engine.connect() as conn:
        counted = sa.select(sa.func.count()).select_from(notes).execution_options(**{HUB_SCOPED_OPTION: True})
        assert conn.execute(counted).scalar_one() == 0


_SCOPED: list[Any] = [
    pytest.param(sa.select(notes).where(notes.c.tenant_id == _T), None, id="select-pinned"),
    pytest.param(sa.select(notes).where(sa.literal(_T) == notes.c.tenant_id), None, id="select-pinned-reversed"),
    pytest.param(
        sa.select(notes).where(sa.and_(notes.c.id > 0, sa.and_(notes.c.tenant_id == _T))),
        None,
        id="select-pinned-in-nested-and",
    ),
    pytest.param(
        sa.select(notes.c.id)
        .join_from(notes, owners, sa.and_(owners.c.id == notes.c.owner_id, owners.c.tenant_id == notes.c.tenant_id))
        .where(notes.c.tenant_id == _T),
        None,
        id="join-pinned-in-on",
    ),
    pytest.param(
        sa.select(notes.c.id)
        .outerjoin_from(notes, owners, sa.and_(owners.c.id == notes.c.owner_id, owners.c.tenant_id == _T))
        .where(notes.c.tenant_id == _T),
        None,
        id="outer-join-pinned-in-on",
    ),
    pytest.param(
        sa.select(notes.c.id, _aliased.c.id).where(
            notes.c.tenant_id == _T, _aliased.c.tenant_id == _T, _aliased.c.id == notes.c.owner_id
        ),
        None,
        id="alias-pinned",
    ),
    pytest.param(
        sa.select(notes).where(
            notes.c.tenant_id == _T,
            ~sa.exists(sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id, owners.c.tenant_id == _T)),
        ),
        None,
        id="correlated-not-exists-pinned",
    ),
    pytest.param(
        _newest_note_per_owner(pinned=True),
        None,
        id="grouped-subquery-pinned",
    ),
    pytest.param(
        sa.union(
            sa.select(notes.c.id).where(notes.c.tenant_id == _T),
            sa.select(owners.c.id).where(owners.c.tenant_id == _T),
        ),
        None,
        id="union-pinned",
    ),
    pytest.param(
        sa.select(notes.c.id).where(
            notes.c.tenant_id == _T,
            notes.c.owner_id.in_(
                sa.select(
                    sa.select(notes.c.owner_id)
                    .where(notes.c.tenant_id == _T)
                    .group_by(notes.c.owner_id)
                    .cte("c")
                    .c.owner_id
                )
            ),
        ),
        None,
        id="cte-pinned",
    ),
    pytest.param(
        sa.select(notes).where(
            notes.c.tenant_id == _T,
            sa.exists(
                sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id, owners.c.tenant_id == _T).correlate(notes)
            ),
        ),
        None,
        id="explicitly-correlated-exists-pinned",
    ),
    pytest.param(
        _update_from_owner_counts(pinned=True),
        None,
        id="update-from-pinned-subquery",
    ),
    pytest.param(
        sa.select(notes.c.id, owners.c.id, _aliased.c.id).where(
            notes.c.tenant_id == _T,
            owners.c.tenant_id == notes.c.tenant_id,
            _aliased.c.tenant_id == owners.c.tenant_id,
            owners.c.id == notes.c.owner_id,
            _aliased.c.owner_id == owners.c.id,
        ),
        None,
        id="pin-reached-through-tenant-equalities",
    ),
    pytest.param(
        sa.select(notes).where(
            notes.c.tenant_id == _T,
            sa.exists(
                sa.select(owners.c.id).where(owners.c.id == notes.c.owner_id, owners.c.tenant_id == notes.c.tenant_id)
            ),
        ),
        None,
        id="correlated-to-the-enclosing-pinned-table",
    ),
    pytest.param(
        sa.select(notes.c.id, owners.c.id)
        .select_from(notes.outerjoin(owners, owners.c.id == notes.c.owner_id, full=True))
        .where(notes.c.tenant_id == _T, owners.c.tenant_id == _T),
        None,
        id="full-join-pinned-in-where",
    ),
    pytest.param(sa.select(sa.literal(1)), None, id="from-less-select"),
    pytest.param(sa.select(tenants), None, id="global-table-carrying-tenant-id"),
    pytest.param(sa.select(plain), None, id="table-without-tenant-id"),
    pytest.param(sa.select(foreign_notes), None, id="table-of-another-metadata"),
    pytest.param(
        sa.update(sa.table("notes", sa.column("body"), sa.column("tenant_id"))).values(body="x"),
        None,
        id="frozen-table-construct",
    ),
    pytest.param(
        sa.update(notes).where(notes.c.tenant_id == _T, notes.c.id == 1).values(body="x"), None, id="update-pinned"
    ),
    pytest.param(sa.delete(notes).where(notes.c.tenant_id == _T), None, id="delete-pinned"),
    pytest.param(sa.insert(notes).values(id=1, tenant_id=_T), None, id="insert-values"),
    pytest.param(sa.insert(notes).values({notes.c.tenant_id: _T, notes.c.id: 1}), None, id="insert-column-keyed"),
    pytest.param(sa.insert(notes), {"id": 1, "tenant_id": _T}, id="insert-params"),
    pytest.param(sa.insert(notes), [{"id": 1, "tenant_id": _T}, {"id": 2, "tenant_id": _T}], id="insert-executemany"),
    pytest.param(
        sa.insert(notes).values([{"id": 1, "tenant_id": _T}, {"id": 2, "tenant_id": _T}]),
        None,
        id="insert-multi-values",
    ),
    pytest.param(sa.insert(notes).values([(1, _T, None, "x")]), None, id="insert-positional-multi-values"),
    pytest.param(
        sa.insert(notes).from_select(
            ["id", "tenant_id"], sa.select(owners.c.id, owners.c.tenant_id).where(owners.c.tenant_id == _T)
        ),
        None,
        id="insert-from-pinned-select",
    ),
    pytest.param(sa.insert(plain).values(id=1), None, id="insert-into-table-without-tenant-id"),
]


@pytest.mark.parametrize(("statement", "params"), _SCOPED)
def test_a_scoped_statement_runs(engine: Engine, statement: Statement, params: Params) -> None:
    _run(engine, statement, params)


# Shapes SQLite cannot run, checked without executing.
_SCOPED_BEYOND_SQLITE: list[Any] = [
    pytest.param(_owner_lateral(pinned=True), id="lateral-pinned"),
    pytest.param(
        _delete_using_owner_counts(pinned=True),
        id="delete-using-pinned-subquery",
    ),
]


@pytest.mark.parametrize("statement", _SCOPED_BEYOND_SQLITE)
def test_a_scoped_statement_beyond_sqlite_passes_the_check(statement: Statement) -> None:
    TenantStatementGuard(_md).check(statement)


_scoped = ScopedStatements(StoreScope(TenantId(_T)))


def _builder_statements() -> list[Any]:
    """What the scoped seam's builders emit, alone and composed."""
    on_owner = owners.c.id == notes.c.owner_id
    return [
        pytest.param(_scoped.select(notes), id="select"),
        pytest.param(_scoped.select(_aliased, _aliased.c.id), id="select-alias"),
        pytest.param(sa.select(_scoped.exists(notes, notes.c.id > 0)), id="exists"),
        pytest.param(_scoped.select(notes, notes.c.id).where(_scoped.exists(owners, on_owner)), id="correlated-exists"),
        pytest.param(
            _scoped.select(notes, notes.c.id).where(~_scoped.exists(owners, on_owner)), id="correlated-not-exists"
        ),
        pytest.param(_scoped.select(_scoped.scope_join(notes, owners, on_owner), notes.c.id), id="scope-join-inner"),
        pytest.param(
            _scoped.select(_scoped.scope_join(notes, owners, on_owner, isouter=True), notes.c.id),
            id="scope-join-outer",
        ),
        pytest.param(
            _scoped.select(
                _scoped.scope_join(
                    _scoped.scope_join(notes, owners, on_owner),
                    _aliased,
                    _aliased.c.owner_id == owners.c.id,
                    isouter=True,
                ),
                notes.c.id,
            ),
            id="scope-join-chained",
        ),
        pytest.param(sa.union(_scoped.select(notes, notes.c.id), _scoped.select(owners, owners.c.id)), id="union"),
        pytest.param(_scoped.insert(notes).values(id=1, body="x"), id="insert"),
        pytest.param(_scoped.update(notes).where(notes.c.id == 1).values(body="y"), id="update"),
        pytest.param(_scoped.delete(notes).where(notes.c.id == 1), id="delete"),
    ]


@pytest.mark.parametrize("statement", _builder_statements())
def test_what_the_scoped_builders_emit_passes_the_guard(engine: Engine, statement: Statement) -> None:
    _run(engine, statement)


def test_a_statement_marked_hub_scoped_runs_unwalked(engine: Engine) -> None:
    _run(engine, sa.select(notes).execution_options(**{HUB_SCOPED_OPTION: True}))
    _run(engine, sa.insert(notes).values(id=1, body="x").execution_options(**{HUB_SCOPED_OPTION: True}))


def test_a_connection_or_engine_marked_hub_scoped_runs_every_statement_unwalked(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execution_options(**{HUB_SCOPED_OPTION: True}).execute(sa.select(notes))
    with engine.execution_options(**{HUB_SCOPED_OPTION: True}).connect() as conn:
        conn.execute(sa.delete(notes))


def test_raising_names_every_unscoped_table_once(engine: Engine) -> None:
    statement = sa.select(notes.c.id).join_from(notes, owners, owners.c.id == notes.c.owner_id)
    with pytest.raises(UnscopedTenantStatement) as raised:
        _run(engine, statement)
    assert raised.value.violations == (
        "select names tenant-owned table notes without a tenant_id equality in its WHERE or join ON",
        "select names tenant-owned table owners without a tenant_id equality in its WHERE or join ON",
    )


def _set_hub_guard(installed: bool) -> None:
    if installed and not event.contains(Engine, "before_execute", guard_hub_statement):
        event.listen(Engine, "before_execute", guard_hub_statement)
    if not installed and event.contains(Engine, "before_execute", guard_hub_statement):
        event.remove(Engine, "before_execute", guard_hub_statement)


@pytest.fixture
def no_hub_guard() -> Iterator[None]:
    """Starts without the process-wide hub guard and leaves the process as it found it."""
    before = event.contains(Engine, "before_execute", guard_hub_statement)
    _set_hub_guard(False)
    yield
    _set_hub_guard(before)


def _select_invitations() -> None:
    engine = sa.create_engine("sqlite://", poolclass=sa.StaticPool)
    schema.metadata.create_all(engine, tables=[schema.tenants, schema.invitations])
    try:
        _run(engine, sa.select(schema.invitations))
    finally:
        engine.dispose()


@pytest.mark.usefixtures("no_hub_guard")
@pytest.mark.parametrize("value", [None, "", "0", "true"], ids=["unset", "empty", "zero", "true"])
def test_the_hub_guard_stays_out_unless_switched_on(value: str | None) -> None:
    environ = {} if value is None else {GUARD_ENVIRONMENT_VARIABLE: value}
    assert install_tenant_guard(environ) is False
    assert not event.contains(Engine, "before_execute", guard_hub_statement)
    _select_invitations()


@pytest.mark.usefixtures("no_hub_guard")
def test_the_hub_guard_installs_once_on_every_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    environ = {GUARD_ENVIRONMENT_VARIABLE: "1"}
    assert install_tenant_guard(environ) is True
    assert install_tenant_guard(environ) is True
    assert install_tenant_guard({}) is True
    with pytest.raises(UnscopedTenantStatement, match="invitations"):
        _select_invitations()

    checks: list[object] = []
    check = TenantStatementGuard.check
    monkeypatch.setattr(
        TenantStatementGuard, "check", lambda self, statement, **kw: checks.append(check(self, statement, **kw))
    )
    engine = sa.create_engine("sqlite://")
    _run(engine, sa.select(sa.literal(1)))
    engine.dispose()
    assert len(checks) == 1


_HUB_PROCESS = textwrap.dedent(
    """
    import sqlalchemy as sa
    from blizzard.hub.app import open_hub_engine
    from blizzard.hub.store import schema
    from blizzard.hub.store.tenant_guard import UnscopedTenantStatement

    engine = open_hub_engine("sqlite://")
    schema.metadata.create_all(engine, tables=[schema.tenants, schema.invitations])
    try:
        with engine.connect() as conn:
            conn.execute(sa.select(schema.invitations))
    except UnscopedTenantStatement:
        print("refused")
    else:
        print("admitted")
    """
)


@pytest.mark.parametrize(("value", "outcome"), [("1", "refused"), (None, "admitted")], ids=["switched-on", "unset"])
def test_a_hub_process_inherits_the_guard_from_its_environment(value: str | None, outcome: str) -> None:
    environ = {key: val for key, val in os.environ.items() if key != GUARD_ENVIRONMENT_VARIABLE}
    if value is not None:
        environ[GUARD_ENVIRONMENT_VARIABLE] = value
    result = subprocess.run(
        [sys.executable, "-c", _HUB_PROCESS], capture_output=True, text=True, check=True, env=environ
    )
    assert result.stdout.strip() == outcome
