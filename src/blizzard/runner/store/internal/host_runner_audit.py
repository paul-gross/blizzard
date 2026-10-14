"""The runner audit: reads that span every runner's rows at once (package-private).

The invariant checker asserts what holds across the whole store — including rows of a runner it does not register —
and ``init`` asks which runners a store's identity rows name before any runner is configured. Neither can carry a runner
equality, so this is the statements' home deliberately unscoped by runner: its connection is exempt from the runner
statement guard."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection

from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION, RunnerStoreConnections
from blizzard.runner.store.schema import runner_identity


class RunnerAudit:
    """Unscoped read connections over the runner store."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def connect(self) -> Connection:
        """A connection every statement on which is exempt from the runner guard; close it (``with``) when done."""
        return self._store.connect().execution_options(**{RUNNER_SCOPED_OPTION: True})

    def named_runners(self) -> tuple[str, ...]:
        """The ids of the runners the store's identity rows name, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(sa.select(runner_identity.c.runner_id).order_by(runner_identity.c.id)).all()
        return tuple(str(row.runner_id) for row in rows)
