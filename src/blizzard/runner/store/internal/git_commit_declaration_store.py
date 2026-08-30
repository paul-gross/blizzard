"""SQLAlchemy adapter for the git-commit declaration repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.git_commit_declaration import (
    GitCommitDeclarationRecord,
    IWriteGitCommitDeclarationRepository,
)
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import git_commit_declarations

_log = get_logger("blizzard.runner.store")


class GitCommitDeclarationStore:
    """Read-write git-commit declaration adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclarationRecord]:
        newest = (
            select(
                git_commit_declarations.c.environment_id,
                git_commit_declarations.c.repo,
                func.max(git_commit_declarations.c.id).label("id"),
            )
            .where(git_commit_declarations.c.lease_id == lease_id)
            .group_by(git_commit_declarations.c.environment_id, git_commit_declarations.c.repo)
            .subquery()
        )
        stmt = select(
            git_commit_declarations.c.environment_id,
            git_commit_declarations.c.repo,
            git_commit_declarations.c.branch,
            git_commit_declarations.c.commit,
        ).join(newest, git_commit_declarations.c.id == newest.c.id)
        return {
            (str(r.environment_id), str(r.repo)): GitCommitDeclarationRecord(
                environment_id=str(r.environment_id),
                repo=str(r.repo),
                branch=str(r.branch),
                commit=str(r.commit),
            )
            for r in self._store.all(stmt)
        }

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
    ) -> None:
        # A single committed transaction — durable the instant this returns, so it survives
        # a `kill -9` right after (issue #143).
        with self._store.begin() as conn:
            conn.execute(
                git_commit_declarations.insert().values(
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
            )
        _log.info("git-commit declaration recorded", lease_id=lease_id, repo=repo)


def _conforms_git_commit_declaration_store(x: GitCommitDeclarationStore) -> IWriteGitCommitDeclarationRepository:
    return x
