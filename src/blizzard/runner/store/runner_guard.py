"""The runner statement guard — a ``before_execute`` listener that refuses a statement reaching a runner-scoped table
without naming its runner.

It is the key-scoping core's walker (:class:`~blizzard.foundation.store.statement_guard.KeyedStatementGuard`) over
``runner/store/schema.py``'s metadata, configured for ``runner_id``
(:data:`~blizzard.runner.store.errors.RUNNER_SCOPING`): every compiled ``Select``, ``Update`` and ``Delete``, nested
ones included, must scope each runner-scoped table it renders by a ``runner_id`` equality, and every ``Insert`` into
one must name ``runner_id``. Host-scoped tables carry no ``runner_id`` and pass. A statement carrying
:data:`~blizzard.runner.store.errors.RUNNER_SCOPED_OPTION`, on itself or on its connection or engine, is deliberately
unscoped and passes unwalked. Raw SQL is not walked, and a migration's frozen ``sa.table()`` constructs are other
objects, so they pass.

It is installed process-wide when the runner opens its engine, and only when the process was started with
``BLIZZARD_RUNNER_GUARD=1`` — production leaves it off."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection

from blizzard.foundation.store.key_scoping import UnscopedKeyedStatement
from blizzard.foundation.store.statement_guard import KeyedStatementGuard
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION, RUNNER_SCOPING

#: The environment variable that switches the runner's guard on, set to ``1``. A subprocess inherits it.
GUARD_ENVIRONMENT_VARIABLE = "BLIZZARD_RUNNER_GUARD"


class UnscopedRunnerStatement(UnscopedKeyedStatement):
    """Raised before a statement executes when it reaches a runner-scoped table without naming its runner."""


#: The runner store's guard over ``schema.metadata``.
RUNNER_GUARD = KeyedStatementGuard(
    schema.metadata, RUNNER_SCOPING, exempt_option=RUNNER_SCOPED_OPTION, unscoped=UnscopedRunnerStatement
)


def guard_runner_statement(
    conn: Connection,
    clauseelement: object,
    multiparams: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    """The class-level ``Engine`` listener :func:`install_runner_guard` registers."""
    RUNNER_GUARD.before_execute(conn, clauseelement, multiparams, params, execution_options)


def install_runner_guard(environ: Mapping[str, str] = os.environ) -> bool:
    """Register :func:`guard_runner_statement` on every ``Engine`` in this process when ``environ`` switches the guard
    on, once however often it is called. Return whether the guard is installed."""
    if environ.get(GUARD_ENVIRONMENT_VARIABLE) != "1":
        return event.contains(Engine, "before_execute", guard_runner_statement)
    if not event.contains(Engine, "before_execute", guard_runner_statement):
        event.listen(Engine, "before_execute", guard_runner_statement)
    return True
