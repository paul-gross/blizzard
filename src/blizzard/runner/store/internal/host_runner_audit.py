"""The invariant checker's runner audit: a connection that reads every runner's rows at once (package-private).

An audit asserts what holds across the whole store — including rows that name no runner, or another — so it cannot
carry a runner equality. It is one of the two statements' homes deliberately unscoped by runner, and marks its
connection exempt from the runner statement guard."""

from __future__ import annotations

from sqlalchemy import Connection

from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION, RunnerStoreConnections


class RunnerAudit:
    """Unscoped read connections over the runner store, for the invariant checker alone."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def connect(self) -> Connection:
        """A connection every statement on which is exempt from the runner guard; close it (``with``) when done."""
        return self._store.connect().execution_options(**{RUNNER_SCOPED_OPTION: True})
