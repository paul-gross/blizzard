"""The runner-store scoping seam: the connections every adapter holds, bound to one runner.

:class:`RunnerStoreConnections` is bound to a **runner binding** — the hub-minted id of the runner whose rows it reads
and writes, or none yet. Its statement builders are the key-scoping core's
(:mod:`blizzard.foundation.store.key_scoping`) under :data:`RUNNER_SCOPING`: every table declared
``info={"scope": "runner"}`` is confined to ``runner_id = <bound>`` (``runner_identity`` by its own ``runner_id``), and
every insert into one stamps it; a host-scoped table passes through untouched. Until bound, a runner-scoped read
answers as an empty store and a runner-scoped write raises :class:`RunnerUnbound`. A binding is set once and never
changes."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from operator import attrgetter
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Connection, Engine, Exists, Row, Select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import Delete, FromClause, Insert, Join, TableClause, Update
from sqlalchemy.sql.compiler import SQLCompiler
from sqlalchemy.sql.elements import BinaryExpression

from blizzard.foundation.store.key_scoping import KeyedStatements, KeyScoping
from blizzard.runner.store.errors import RunnerStoreErrorFactory

#: The column every runner-scoped table is keyed by.
RUNNER_COLUMN = "runner_id"

#: The execution option that marks a statement deliberately unscoped by runner, exempting it from the runner statement
#: guard: the cross-runner reads of :class:`~blizzard.runner.store.internal.host_runner_audit.RunnerAudit`.
RUNNER_UNSCOPED_OPTION = "blizzard_runner_unscoped"


class RunnerUnbound(RuntimeError):
    """Raised when a runner-scoped write is built before the store is bound to a runner: no row may be written that
    names no runner."""

    def __init__(self, table: str) -> None:
        super().__init__(f"runner store is not bound to a runner yet; cannot write {table}")
        self.table = table


class RunnerAlreadyBound(ValueError):
    """Raised when a bound store is asked to bind to another runner: a binding never changes."""

    def __init__(self, bound: str, requested: str) -> None:
        super().__init__(f"runner store is bound to {bound}; it cannot be rebound to {requested}")
        self.bound = bound
        self.requested = requested


class RunnerKeySupplied(ValueError):
    """Raised when a caller supplies ``runner_id`` for a runner-scoped table; the scoped insert stamps it."""

    def __init__(self, table: str) -> None:
        super().__init__(f"runner_id supplied for {table}; the scoped insert stamps it")
        self.table = table


def _runner_scoped(table: TableClause) -> bool:
    info = getattr(table, "info", None)
    return isinstance(info, Mapping) and cast(Mapping[str, object], info).get("scope") == "runner"


class _LikelyRunnerEquality(BinaryExpression[bool]):
    """``runner_id = <bound>``, which SQLite plans as ``likely(...)``.

    No index leads with ``runner_id`` yet and every row a store holds is its one runner's, so the equality filters
    nothing; left as a plain term, SQLite's planner counts it as selective and trades an ordered index walk for a scan
    and a sort. ``likely`` keeps each read's plan the one it had before the key existed. Other dialects render the plain
    equality, and the statement guard reads it as one."""

    inherit_cache = True


@compiles(_LikelyRunnerEquality, "sqlite")
def _compile_likely_runner_equality(element: _LikelyRunnerEquality, compiler: SQLCompiler, **kw: Any) -> str:
    visit_binary = cast(Callable[..., str], attrgetter("visit_binary")(compiler))
    return f"likely({visit_binary(element, **kw)})"


def _runner_equality(column: ColumnElement[Any], key: Any) -> ColumnElement[bool]:
    equality = column == key
    assert isinstance(equality, BinaryExpression)
    return _LikelyRunnerEquality(
        equality.left,
        equality.right,
        equality.operator,
        type_=cast(sa.types.TypeEngine[Any], attrgetter("type")(cast(BinaryExpression[Any], equality))),
    )


#: The runner store's partition of its tables by runner: keyed by ``runner_id``, every table declared
#: ``info={"scope": "runner"}``.
RUNNER_SCOPING = KeyScoping(
    key_column=RUNNER_COLUMN,
    owns=_runner_scoped,
    owner="runner-scoped",
    key_supplied=RunnerKeySupplied,
    key_equality=_runner_equality,
)


class RunnerStoreConnections:
    """The connection-acquiring collaborator every ``runner/store/internal/`` adapter
    takes in place of ``Engine`` (``bzh:dependency-injection``), bound to one runner (or none yet), and the statement
    builders that confine every runner-scoped table to it.

    Builders read the binding as they build, so one instance shared by every adapter serves both sides of
    :meth:`bind`."""

    def __init__(self, engine: Engine, errors: RunnerStoreErrorFactory, runner_id: str | None = None) -> None:
        self._engine = engine
        self._errors = errors
        self._runner_id = runner_id
        self._binding = threading.Lock()

    # --- the binding -------------------------------------------------------------------------------------------------

    @property
    def runner_id(self) -> str | None:
        """The runner this store is bound to; ``None`` until bound."""
        return self._runner_id

    def bound_runner_id(self) -> str:
        """The runner this store is bound to; raises :class:`RunnerUnbound` until bound."""
        if self._runner_id is None:
            raise RunnerUnbound("runner_id")
        return self._runner_id

    def bind(self, runner_id: str) -> None:
        """Bind to ``runner_id``; binding again to the same id is a no-op, to another raises
        :class:`RunnerAlreadyBound`."""
        with self._binding:
            if self._runner_id is None:
                self._runner_id = runner_id
            elif self._runner_id != runner_id:
                raise RunnerAlreadyBound(self._runner_id, runner_id)

    # --- scoped builders ---------------------------------------------------------------------------------------------

    def _reading(self) -> KeyedStatements:
        """Builders for a read: bound to the runner, or — unbound — to a ``NULL`` parameter no row equals, so a
        runner-scoped read answers as an empty store."""
        key: Any = self._runner_id
        if key is None:
            key = sa.bindparam(None, None, type_=sa.String(), unique=True)
        return KeyedStatements(RUNNER_SCOPING, key)

    def _writing(self, table: TableClause) -> KeyedStatements:
        """Builders for a write of ``table``; a runner-scoped table refuses while unbound."""
        if self._runner_id is None and RUNNER_SCOPING.is_keyed(table):
            raise RunnerUnbound(table.name)
        return KeyedStatements(RUNNER_SCOPING, self._runner_id)

    def key_predicates(self, from_: FromClause) -> list[ColumnElement[bool]]:
        """``runner_id = <bound>`` for each runner-scoped table a read of ``from_`` returns rows of unconditionally."""
        return self._reading().key_predicates(from_)

    def select(self, from_: FromClause, *cols: Any) -> Select[Any]:
        """``SELECT cols FROM from_`` confined to the bound runner (see :meth:`KeyedStatements.select`)."""
        return self._reading().select(from_, *cols)

    def exists(self, from_: FromClause, *criteria: ColumnElement[bool]) -> Exists:
        """A correlatable ``EXISTS`` confined to the bound runner."""
        return self._reading().exists(from_, *criteria)

    def scope_join(
        self, left: FromClause, right: FromClause, onclause: ColumnElement[bool], *, isouter: bool = False
    ) -> Join:
        """``left JOIN right`` with the right side confined in the ``ON``; the left side is the reading select's."""
        return self._reading().scope_join(left, right, onclause, isouter=isouter)

    def insert(self, table: TableClause) -> Insert:
        """``INSERT INTO table`` with the bound runner stamped."""
        return self._writing(table).insert(table)

    def insert_rows(self, table: TableClause, rows: Sequence[Mapping[Any, Any]]) -> Insert:
        """One multi-row ``INSERT`` with the bound runner stamped into each row."""
        return self._writing(table).insert_rows(table, rows)

    def insert_from_select(self, table: TableClause, names: Sequence[str], select: Select[Any]) -> Insert:
        """``INSERT … SELECT`` with the bound runner stamped as a literal column; ``select`` is the caller's to
        confine."""
        return self._writing(table).insert_from_select(table, names, select)

    def update(self, table: TableClause) -> Update:
        """``UPDATE table`` confined to the bound runner's rows."""
        return self._writing(table).update(table)

    def delete(self, table: TableClause) -> Delete:
        """``DELETE FROM table`` confined to the bound runner's rows."""
        return self._writing(table).delete(table)

    # --- connections -------------------------------------------------------------------------------------------------

    def connect(self) -> Connection:
        try:
            return self._engine.connect()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="connect") from exc

    def begin(self) -> AbstractContextManager[Connection]:
        try:
            return self._engine.begin()
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="begin") from exc

    def all(self, stmt: Select[Any]) -> list[Row[Any]]:
        try:
            with self._engine.connect() as conn:
                return list(conn.execute(stmt))
        except SQLAlchemyError as exc:
            raise self._errors.from_driver(exc, operation="query") from exc
