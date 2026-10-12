"""SQLAlchemy adapter for the environment-binding repository seam (package-private)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import bindparam, func, or_

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.repository import (
    BindingCompletion,
    EnvBinding,
    IncompleteBinding,
    IWriteEnvironmentRepository,
)
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.base import HELD_BINDING
from blizzard.runner.store.schema import binding_releases, env_bindings, leases

_log = get_logger("blizzard.runner.store")


class EnvironmentStore:
    """Read-write environment-binding adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def held_environment_ids(self) -> list[str]:
        stmt = (
            self._store.select(env_bindings, env_bindings.c.environment_id)
            .where(HELD_BINDING.clause(self._store))
            .distinct()
        )
        return [str(r.environment_id) for r in self._store.all(stmt)]

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBinding]:
        stmt = (
            self._store.select(env_bindings)
            .where(env_bindings.c.chunk_id == chunk_id)
            .where(HELD_BINDING.clause(self._store))
            .order_by(env_bindings.c.bound_at)
        )
        return [self._row_to_binding(r) for r in self._store.all(stmt)]

    def live_tenure_chunk_ids(self) -> list[str]:
        stmt = (
            self._store.select(env_bindings, env_bindings.c.chunk_id).where(HELD_BINDING.clause(self._store)).distinct()
        )
        return [str(r.chunk_id) for r in self._store.all(stmt)]

    def held_bindings(self) -> list[EnvBinding]:
        stmt = (
            self._store.select(env_bindings).where(HELD_BINDING.clause(self._store)).order_by(env_bindings.c.bound_at)
        )
        return [self._row_to_binding(r) for r in self._store.all(stmt)]

    def record_binding(
        self,
        *,
        chunk_id: str,
        environment_id: str,
        workdir: str,
        bound_at: datetime,
        workspace_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        with self._store.begin() as conn:
            conn.execute(
                self._store.insert(env_bindings).values(
                    chunk_id=chunk_id,
                    environment_id=environment_id,
                    workdir=workdir,
                    bound_at=bound_at,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
            )
        _log.info("env bound", chunk_id=chunk_id, environment_id=environment_id, workdir=workdir)

    def incomplete_held_bindings(self) -> list[IncompleteBinding]:
        stmt = (
            self._store.select(
                env_bindings,
                env_bindings.c.id,
                env_bindings.c.chunk_id,
                env_bindings.c.workdir,
                env_bindings.c.workspace_id,
                env_bindings.c.project_id,
            )
            .where(HELD_BINDING.clause(self._store))
            .where(or_(env_bindings.c.workspace_id.is_(None), env_bindings.c.project_id.is_(None)))
            .order_by(env_bindings.c.id)
        )
        return [
            IncompleteBinding(
                binding_id=int(r.id),
                chunk_id=str(r.chunk_id),
                workdir=str(r.workdir),
                workspace_id=r.workspace_id,
                project_id=r.project_id,
            )
            for r in self._store.all(stmt)
        ]

    def complete_bindings(self, completions: Sequence[BindingCompletion]) -> None:
        if not completions:
            return
        # One executemany over every row; COALESCE keeps a column already set — completion only fills a gap.
        stmt = (
            self._store.update(env_bindings)
            .where(env_bindings.c.id == bindparam("b_id"))
            .values(
                workspace_id=func.coalesce(env_bindings.c.workspace_id, bindparam("b_workspace_id")),
                project_id=func.coalesce(env_bindings.c.project_id, bindparam("b_project_id")),
            )
        )
        with self._store.begin() as conn:
            conn.execute(
                stmt,
                [
                    {"b_id": c.binding_id, "b_workspace_id": c.workspace_id, "b_project_id": c.project_id}
                    for c in completions
                ],
            )
        _log.info("held bindings completed", count=len(completions))

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        with self._store.begin() as conn:
            floor = conn.execute(
                self._store.select(leases, func.max(leases.c.epoch)).where(leases.c.chunk_id == chunk_id)
            ).scalar_one()
            conn.execute(
                self._store.insert(binding_releases).values(
                    chunk_id=chunk_id,
                    environment_id=environment_id,
                    released_at=released_at,
                    lease_epoch_floor=floor,
                )
            )
        _log.info("env released", chunk_id=chunk_id, environment_id=environment_id)

    @staticmethod
    def _row_to_binding(r) -> EnvBinding:  # type: ignore[no-untyped-def]
        return EnvBinding(
            chunk_id=str(r.chunk_id),
            environment_id=str(r.environment_id),
            workdir=str(r.workdir),
            bound_at=r.bound_at,
            workspace_id=r.workspace_id,
            project_id=r.project_id,
        )


def _conforms_environment_store(x: EnvironmentStore) -> IWriteEnvironmentRepository:
    return x
