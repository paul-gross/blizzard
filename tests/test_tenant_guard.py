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
from blizzard.hub.store.tenant_guard import (
    GUARD_ENVIRONMENT_VARIABLE,
    TenantStatementGuard,
    UnscopedTenantStatement,
    guard_hub_statement,
    install_tenant_guard,
)
from blizzard.hub.tenancy import HUB_SCOPED_OPTION

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
