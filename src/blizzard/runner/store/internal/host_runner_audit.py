"""The runner audit: reads that span every runner's rows at once (package-private).

The invariant checker asserts what holds across the whole store — including rows of a runner it does not register —
and ``init`` asks which runners a store's identity rows name before any runner is configured. Neither can carry a runner
equality, so this is the statements' home deliberately unscoped by runner: its connection is exempt from the runner
statement guard. So is a workspace's pool, which every runner of the host draws from: what any of them holds in it is
read here."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection

from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.store.schema import binding_releases, env_bindings, runner_identity
from blizzard.runner.store.scoped import RUNNER_COLUMN, RUNNER_UNSCOPED_OPTION, RunnerStoreConnections


class RunnerAudit:
    """Unscoped read connections over the runner store."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def connect(self) -> Connection:
        """A connection every statement on which is exempt from the runner guard; close it (``with``) when done."""
        return self._store.connect().execution_options(**{RUNNER_UNSCOPED_OPTION: True})

    def named_runners(self) -> tuple[str, ...]:
        """The ids of the runners the store's identity rows name, oldest first."""
        with self.connect() as conn:
            rows = conn.execute(sa.select(runner_identity.c.runner_id).order_by(runner_identity.c.id)).all()
        return tuple(str(row.runner_id) for row in rows)

    def held_workspace_bindings(self, workspace_id: str) -> list[EnvBinding]:
        """Every runner's held bindings in ``workspace_id``'s pool, plus every held binding naming no workspace — a
        row of unknown workspace may be in any pool, so it only ever shrinks one. Oldest first."""
        released = (
            sa.select(binding_releases.c.id)
            .where(binding_releases.c[RUNNER_COLUMN] == env_bindings.c[RUNNER_COLUMN])
            .where(binding_releases.c.chunk_id == env_bindings.c.chunk_id)
            .where(binding_releases.c.environment_id == env_bindings.c.environment_id)
            .where(binding_releases.c.seq > env_bindings.c.seq)
            .exists()
        )
        stmt = (
            sa.select(env_bindings)
            .where(~released)
            .where(sa.or_(env_bindings.c.workspace_id == workspace_id, env_bindings.c.workspace_id.is_(None)))
            .order_by(env_bindings.c.bound_at, env_bindings.c.id)
        )
        with self.connect() as conn:
            rows = conn.execute(stmt).all()
        return [
            EnvBinding(
                chunk_id=str(r.chunk_id),
                environment_id=str(r.environment_id),
                workdir=str(r.workdir),
                bound_at=r.bound_at,
                workspace_id=r.workspace_id,
                project_id=r.project_id,
            )
            for r in rows
        ]
