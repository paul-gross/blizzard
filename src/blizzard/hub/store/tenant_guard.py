"""The tenant statement guard — a ``before_execute`` listener that refuses a statement reaching a tenant-owned table
without naming its tenant.

It is ``src/blizzard/foundation/store/statement_guard.py``'s ``KeyedStatementGuard`` configured for ``tenant_id``,
watching ``schema.metadata``'s tables; its exemption option is :data:`~blizzard.hub.tenancy.HUB_SCOPED_OPTION`. It is
installed process-wide, and only when the process was started
with ``BLIZZARD_TENANT_GUARD=1`` — production leaves it off.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Engine, MetaData, event
from sqlalchemy.engine import Connection

from blizzard.foundation.store.key_scoping import UnscopedKeyedStatement
from blizzard.foundation.store.statement_guard import KeyedStatementGuard
from blizzard.hub.store import schema
from blizzard.hub.store.scoped import TENANT_SCOPING
from blizzard.hub.tenancy import HUB_SCOPED_OPTION

#: The environment variable that switches the hub's guard on, set to ``1``. A subprocess inherits it.
GUARD_ENVIRONMENT_VARIABLE = "BLIZZARD_TENANT_GUARD"


class UnscopedTenantStatement(UnscopedKeyedStatement):
    """Raised before a statement executes when it reaches a tenant-owned table without naming its tenant."""


class TenantStatementGuard(KeyedStatementGuard):
    """The key-scoping guard (:class:`KeyedStatementGuard`) checking statements against the tenant-owned tables of one
    ``MetaData``."""

    def __init__(self, metadata: MetaData) -> None:
        super().__init__(metadata, TENANT_SCOPING, exempt_option=HUB_SCOPED_OPTION, unscoped=UnscopedTenantStatement)


_HUB_GUARD = TenantStatementGuard(schema.metadata)


def guard_hub_statement(
    conn: Connection,
    clauseelement: object,
    multiparams: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    execution_options: Mapping[str, Any],
) -> None:
    """The class-level ``Engine`` listener :func:`install_tenant_guard` registers: the hub's guard over
    ``schema.metadata``."""
    _HUB_GUARD.before_execute(conn, clauseelement, multiparams, params, execution_options)


def install_tenant_guard(environ: Mapping[str, str] = os.environ) -> bool:
    """Register :func:`guard_hub_statement` on every ``Engine`` in this process when ``environ`` switches the guard on,
    once however often it is called. Return whether the guard is installed."""
    if environ.get(GUARD_ENVIRONMENT_VARIABLE) != "1":
        return event.contains(Engine, "before_execute", guard_hub_statement)
    if not event.contains(Engine, "before_execute", guard_hub_statement):
        event.listen(Engine, "before_execute", guard_hub_statement)
    return True
