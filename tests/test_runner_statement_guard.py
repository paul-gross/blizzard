"""The runner statement guard and the runner store's scoped seam (unit tier).

The guard refuses every planted statement that reaches a runner-scoped table without a ``runner_id`` equality — a bare
read, an unscoped join side, alias or correlated subquery, an unconfined update or delete, an insert naming no runner —
and admits the same statements built by :class:`RunnerStoreConnections`, a host-scoped table, and a statement carrying
the exemption option. The seam itself: unbound, a runner-scoped read answers as empty and a write raises
:class:`RunnerUnbound`; a binding is set once."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, event

from blizzard.runner.store import schema
from blizzard.runner.store.errors import (
    RUNNER_SCOPED_OPTION,
    RunnerAlreadyBound,
    RunnerKeySupplied,
    RunnerStoreConnections,
    RunnerUnbound,
)
from blizzard.runner.store.runner_guard import (
    GUARD_ENVIRONMENT_VARIABLE,
    RUNNER_GUARD,
    UnscopedRunnerStatement,
    guard_runner_statement,
    install_runner_guard,
)
from tests.runner_fakes import runner_store_errors

pytestmark = pytest.mark.unit

_A = "rn_a"
_AT = sa.literal("2026-01-01")

leases = schema.leases
lease_closures = schema.lease_closures
runner_identity = schema.runner_identity
workspace_prompt = schema.workspace_prompt


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = sa.create_engine("sqlite://", poolclass=sa.StaticPool)
    schema.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _store(engine: Engine, runner_id: str | None = _A) -> RunnerStoreConnections:
    return RunnerStoreConnections(engine, runner_store_errors(), runner_id)


_later = leases.alias("later")

_UNSCOPED: dict[str, Any] = {
    "bare select": sa.select(leases),
    "select of a column": sa.select(leases.c.lease_id).where(leases.c.chunk_id == "ch"),
    "runner_id compared to a non-value": sa.select(leases).where(leases.c.runner_id == leases.c.chunk_id),
    "inner join, one side unscoped": sa.select(leases)
    .join(lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
    .where(leases.c.runner_id == _A),
    "an outer join's right side left unscoped": sa.select(leases)
    .outerjoin(lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
    .where(leases.c.runner_id == _A),
    "an unscoped alias": sa.select(_later).where(_later.c.chunk_id == "ch"),
    "a correlated NOT EXISTS whose rows are unscoped": sa.select(leases)
    .where(leases.c.runner_id == _A)
    .where(~sa.select(lease_closures.c.lease_id).where(lease_closures.c.lease_id == leases.c.lease_id).exists()),
    "a NOT IN subquery left unscoped": sa.select(leases)
    .where(leases.c.runner_id == _A)
    .where(leases.c.lease_id.not_in(sa.select(lease_closures.c.lease_id))),
    "a scalar subquery left unscoped": sa.select(
        leases.c.lease_id, sa.select(sa.func.max(_later.c.epoch)).scalar_subquery()
    ).where(leases.c.runner_id == _A),
    "an unconfined update": sa.update(leases).values(pid=1),
    "an update confined by another column": sa.update(leases).where(leases.c.lease_id == "l").values(pid=1),
    "an unconfined delete": sa.delete(lease_closures),
    "an insert naming no runner": sa.insert(lease_closures).values(
        lease_id="l", chunk_id="c", node_id="n", reason="r", closed_at=_AT
    ),
    "the identity row, read unconfined": sa.select(runner_identity.c.runner_id),
    "the identity row, updated unconfined": sa.update(runner_identity).values(runner_name="x"),
}


@pytest.mark.parametrize("statement", list(_UNSCOPED.values()), ids=list(_UNSCOPED))
def test_the_guard_refuses_a_statement_reaching_a_runner_table_without_its_runner(statement: Any) -> None:
    with pytest.raises(UnscopedRunnerStatement, match="runner-scoped table"):
        RUNNER_GUARD.check(statement)


def _scoped(store: RunnerStoreConnections) -> dict[str, Any]:
    later = leases.alias("later")
    return {
        "a scoped select": store.select(leases).where(leases.c.chunk_id == "ch"),
        "a scoped inner join": store.select(
            sa.join(leases, lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
        ),
        "a scoped outer join": store.select(
            store.scope_join(leases, lease_closures, lease_closures.c.lease_id == leases.c.lease_id, isouter=True)
        ),
        "a scoped alias": store.select(later),
        "a scoped NOT EXISTS": store.select(leases).where(
            ~store.exists(lease_closures, lease_closures.c.lease_id == leases.c.lease_id)
        ),
        "a scoped update": store.update(leases).where(leases.c.lease_id == "l").values(pid=1),
        "a scoped delete": store.delete(lease_closures),
        "a stamped insert": store.insert(lease_closures).values(
            lease_id="l", chunk_id="c", node_id="n", reason="r", closed_at=_AT
        ),
        "the identity row, scoped": store.select(runner_identity),
        "a host-scoped table, untouched": sa.select(workspace_prompt),
        "an exempt statement": sa.select(leases).execution_options(**{RUNNER_SCOPED_OPTION: True}),
    }


def test_the_guard_admits_what_the_seam_builds_a_host_table_and_an_exempt_statement(engine: Engine) -> None:
    statements = _scoped(_store(engine))
    event.listen(engine, "before_execute", RUNNER_GUARD.before_execute)
    try:
        for name, statement in statements.items():
            with engine.begin() as conn:
                conn.execute(statement)
            assert name
    finally:
        event.remove(engine, "before_execute", RUNNER_GUARD.before_execute)


def test_an_unbound_store_reads_as_empty_and_refuses_to_write(engine: Engine) -> None:
    bound = _store(engine)
    with engine.begin() as conn:
        conn.execute(bound.insert(leases).values(lease_id="l", chunk_id="c", epoch=1, created_at=_AT))
    unbound = _store(engine, None)

    assert unbound.all(unbound.select(leases)) == []
    RUNNER_GUARD.check(unbound.select(leases))
    for build in (unbound.insert, unbound.update, unbound.delete):
        with pytest.raises(RunnerUnbound, match="leases"):
            build(leases)
    with pytest.raises(RunnerUnbound):
        unbound.insert_rows(leases, [{"lease_id": "m"}])
    # A host-scoped table names no runner, so an unbound store writes it.
    unbound.insert(workspace_prompt)


def test_a_binding_is_set_once_and_never_changes(engine: Engine) -> None:
    store = _store(engine, None)
    store.bind(_A)
    store.bind(_A)
    assert store.runner_id == _A
    with pytest.raises(RunnerAlreadyBound):
        store.bind("rn_b")
    assert store.runner_id == _A


def test_an_unbound_store_never_adopts_an_identity_row_it_finds(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(_store(engine).insert(leases).values(lease_id="l", chunk_id="c", epoch=1, created_at=_AT))
        conn.execute(_store(engine).insert(runner_identity).values(runner_name="r", registered_at=_AT))
    view = _store(engine, None)

    assert view.all(view.select(leases)) == []
    assert view.all(view.select(runner_identity)) == []
    assert view.runner_id is None  # whose store this is is configured, never read back out of it

    view.bind(_A)
    assert [row.lease_id for row in view.all(view.select(leases))] == ["l"]


def test_a_caller_never_supplies_the_runner_the_seam_stamps(engine: Engine) -> None:
    with pytest.raises(RunnerKeySupplied):
        _store(engine).insert_rows(leases, [{"lease_id": "l", "runner_id": "rn_b"}])


@pytest.fixture
def no_runner_guard() -> Iterator[None]:
    """Starts without the process-wide runner guard and leaves the process as it found it."""
    before = event.contains(Engine, "before_execute", guard_runner_statement)
    if before:
        event.remove(Engine, "before_execute", guard_runner_statement)
    yield
    if before and not event.contains(Engine, "before_execute", guard_runner_statement):
        event.listen(Engine, "before_execute", guard_runner_statement)
    elif not before and event.contains(Engine, "before_execute", guard_runner_statement):
        event.remove(Engine, "before_execute", guard_runner_statement)


def _select_leases() -> None:
    engine = sa.create_engine("sqlite://", poolclass=sa.StaticPool)
    schema.metadata.create_all(engine, tables=[leases])
    try:
        with engine.connect() as conn:
            conn.execute(sa.select(leases))
    finally:
        engine.dispose()


@pytest.mark.usefixtures("no_runner_guard")
@pytest.mark.parametrize("value", [None, "", "0", "true"], ids=["unset", "empty", "zero", "true"])
def test_the_runner_guard_stays_out_unless_switched_on(value: str | None) -> None:
    environ = {} if value is None else {GUARD_ENVIRONMENT_VARIABLE: value}
    assert install_runner_guard(environ) is False
    _select_leases()


@pytest.mark.usefixtures("no_runner_guard")
def test_the_runner_guard_installs_once_on_every_engine() -> None:
    environ = {GUARD_ENVIRONMENT_VARIABLE: "1"}
    assert install_runner_guard(environ) is True
    assert install_runner_guard(environ) is True
    assert install_runner_guard({}) is True
    with pytest.raises(UnscopedRunnerStatement, match="leases"):
        _select_leases()


_RUNNER_PROCESS = textwrap.dedent(
    """
    import sys
    import sqlalchemy as sa
    from blizzard.runner.config import RunnerConfig
    from blizzard.runner.composition import build_runner_process
    from blizzard.runner.store import schema
    from blizzard.runner.store.runner_guard import UnscopedRunnerStatement

    from pathlib import Path
    root = Path(sys.argv[1])
    graph = build_runner_process(RunnerConfig(root=root, db_url=f"sqlite:///{root / 'host.db'}"), runner_id="rn_a")
    try:
        schema.metadata.create_all(graph.engine, tables=[schema.leases])
        with graph.engine.connect() as conn:
            conn.execute(sa.select(schema.leases))
    except UnscopedRunnerStatement:
        print("refused")
    else:
        print("admitted")
    finally:
        graph.close()
    """
)


@pytest.mark.parametrize(("value", "outcome"), [("1", "refused"), (None, "admitted")], ids=["switched-on", "unset"])
def test_a_runner_process_installs_the_guard_from_its_environment(
    tmp_path: Any, value: str | None, outcome: str
) -> None:
    environ = {key: val for key, val in os.environ.items() if key != GUARD_ENVIRONMENT_VARIABLE}
    if value is not None:
        environ[GUARD_ENVIRONMENT_VARIABLE] = value
    result = subprocess.run(
        [sys.executable, "-c", _RUNNER_PROCESS, str(tmp_path)], capture_output=True, text=True, check=True, env=environ
    )
    assert result.stdout.strip().splitlines()[-1] == outcome
