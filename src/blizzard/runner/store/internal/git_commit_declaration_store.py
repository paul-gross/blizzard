"""SQLAlchemy adapter for the git-commit declaration repository seam (package-private)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.batching import id_batches
from blizzard.runner.leases.git_commit_declaration import DeclaredLeaseCommit, GitCommitDeclaration
from blizzard.runner.leases.lease_lock import ILeaseUnit
from blizzard.runner.lifecycle.judgement.git_commit_declaration import IWriteGitCommitDeclarationRepository
from blizzard.runner.store.internal.base import unit_conn
from blizzard.runner.store.schema import git_commit_declarations
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class GitCommitDeclarationStore:
    """Read-write git-commit declaration adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def git_commit_declarations_for_lease(
        self, lease_id: str, *, unit: ILeaseUnit | None = None
    ) -> dict[tuple[str, str], GitCommitDeclaration]:
        newest = (
            self._store.select(
                git_commit_declarations,
                git_commit_declarations.c.environment_id,
                git_commit_declarations.c.repo,
                func.max(git_commit_declarations.c.id).label("id"),
            )
            .where(git_commit_declarations.c.lease_id == lease_id)
            .group_by(git_commit_declarations.c.environment_id, git_commit_declarations.c.repo)
            .subquery()
        )
        stmt = self._store.select(
            git_commit_declarations,
            git_commit_declarations.c.environment_id,
            git_commit_declarations.c.repo,
            git_commit_declarations.c.branch,
            git_commit_declarations.c.commit,
        ).join(newest, git_commit_declarations.c.id == newest.c.id)
        return {
            (str(r.environment_id), str(r.repo)): GitCommitDeclaration(
                environment_id=str(r.environment_id),
                repo=str(r.repo),
                branch=str(r.branch),
                commit=str(r.commit),
            )
            for r in (self._store.all(stmt) if unit is None else unit_conn(unit).execute(stmt).all())
        }

    def git_commit_declarations_for_leases(self, lease_ids: Sequence[str]) -> dict[str, list[DeclaredLeaseCommit]]:
        if not lease_ids:
            return {}
        decl = git_commit_declarations
        result: dict[str, list[DeclaredLeaseCommit]] = {}
        with self._store.connect() as conn:
            for batch in id_batches(lease_ids):
                newest = (
                    self._store.select(decl, func.max(decl.c.id).label("id"))
                    .where(decl.c.lease_id.in_(batch))
                    .group_by(decl.c.lease_id, decl.c.environment_id, decl.c.repo)
                    .subquery()
                )
                stmt = (
                    self._store.select(
                        decl,
                        decl.c.lease_id,
                        decl.c.environment_id,
                        decl.c.repo,
                        decl.c.branch,
                        decl.c.commit,
                        decl.c.declared_at,
                    )
                    .join(newest, decl.c.id == newest.c.id)
                    .order_by(decl.c.lease_id, decl.c.environment_id, decl.c.repo)
                )
                for r in conn.execute(stmt):
                    result.setdefault(str(r.lease_id), []).append(
                        DeclaredLeaseCommit(
                            environment_id=str(r.environment_id),
                            repo=str(r.repo),
                            branch=str(r.branch),
                            commit=str(r.commit),
                            declared_at=r.declared_at,
                        )
                    )
        return result

    def record_git_commit_declaration(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        environment_id: str,
        repo: str,
        branch: str,
        commit: str,
        declared_at: datetime,
        unit: ILeaseUnit | None = None,
    ) -> None:
        # Alone, a single committed transaction — durable the instant this returns, so it survives a `kill -9`
        # right after. In a unit, it commits with the unit.
        stmt = self._store.insert(git_commit_declarations).values(
            lease_id=lease_id,
            chunk_id=chunk_id,
            node_id=node_id,
            epoch=epoch,
            environment_id=environment_id,
            repo=repo,
            branch=branch,
            commit=commit,
            declared_at=declared_at,
        )
        if unit is None:
            with self._store.begin() as conn:
                conn.execute(stmt)
        else:
            unit_conn(unit).execute(stmt)
        _log.info("git-commit declaration recorded", lease_id=lease_id, repo=repo)


_git_commit_declaration_store_conforms: type[IWriteGitCommitDeclarationRepository] = GitCommitDeclarationStore
