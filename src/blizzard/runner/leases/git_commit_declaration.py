"""A worker's git-commit declarations: the declaration value, the row it appends, the refusals
it raises, and the read seam over the recorded rows.

The write and its orchestration live with the lifecycle's judgement; this is the part the
lease read needs too, so it sits under ``leases`` (``bzh:domain-package-layers``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.worker_lease import WorkerLease, WorkerVerb

__all__ = [
    "DeclaredLeaseCommit",
    "GitCommitDeclaration",
    "GitCommitDeclarationOnClosedLease",
    "GitCommitDeclarationRecord",
    "GitCommitDeclarationTooLate",
    "GitCommitDeclarationUnknownRepo",
    "IReadGitCommitDeclarationRepository",
    "require_listed_repo",
]


class GitCommitDeclarationUnknownRepo(Exception):
    """The declared ``(env, repo)`` is not in the lease's environments — mapped to ``400``. An error at
    declare time rather than a drop later: the worker is alive and can re-run the verb correctly."""


class GitCommitDeclarationOnClosedLease(Exception):
    """The declaration names a lease whose standing does not accept a git-commit — mapped to
    ``409``. :data:`~blizzard.runner.leases.worker_lease.WORKER_VERBS` decides which standings
    accept one."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(f"lease {lease_id} is closed and does not accept a git-commit declaration")
        self.lease_id = lease_id


class GitCommitDeclarationTooLate(Exception):
    """The lease's outcome is already buffered for the hub: a declaration now would be recorded
    and never submitted, so it is refused — mapped to ``409``."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(
            f"lease {lease_id}'s outcome is already buffered for submission — a git-commit declared now "
            "would never be submitted; declare commits before the node-step's verdict"
        )
        self.lease_id = lease_id


def require_listed_repo(environment_id: str, repo: str, known: Sequence[str]) -> None:
    """Refuse a repo the environment's manifest does not list, naming the ones it does."""
    if repo not in known:
        raise GitCommitDeclarationUnknownRepo(
            f"environment {environment_id!r} has no repo {repo!r} — it holds {sorted(known)}"
        )


@domain_model
@dataclass(frozen=True)
class GitCommitDeclarationRecord:
    """The declaration row to append: the declaration, the lease, chunk, node and epoch
    that made it, and the instant. ``rides_completion`` is ``False`` for a declaration
    against a closed lease, which no completion will carry."""

    lease_id: str
    chunk_id: str
    node_id: str
    epoch: int
    environment_id: str
    repo: str
    branch: str
    commit: str
    declared_at: datetime
    rides_completion: bool


@domain_model
@dataclass(frozen=True)
class GitCommitDeclaration:
    """A worker's explicit git-commit declaration for one repo in one environment.

    Carries no forge: the origin it is verified against is read from the environment's
    repo manifest. ``environment_id`` is part of the identity, never a decoration."""

    environment_id: str
    repo: str
    branch: str
    commit: str

    def declared_by(
        self, worker: WorkerLease, *, known_repos: Sequence[str], outcome_pending: bool, at: datetime
    ) -> GitCommitDeclarationRecord:
        """The row ``worker``'s lease appends for this declaration at ``at``.

        Refuses a repo the environment does not list, a lease whose standing does not accept a git-commit,
        and a declaration once the lease's outcome is buffered (it would never be submitted)."""
        require_listed_repo(self.environment_id, self.repo, known_repos)
        lease = worker.lease
        if not worker.accepts(WorkerVerb.GIT_COMMIT):
            raise GitCommitDeclarationOnClosedLease(lease.lease_id)
        if outcome_pending:
            raise GitCommitDeclarationTooLate(lease.lease_id)
        return GitCommitDeclarationRecord(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            environment_id=self.environment_id,
            repo=self.repo,
            branch=self.branch,
            commit=self.commit,
            declared_at=at,
            rides_completion=worker.active,
        )


@domain_model
@dataclass(frozen=True)
class DeclaredLeaseCommit:
    """One lease's newest declaration for one ``(environment_id, repo)``, with when it was
    declared — the lease read's own record of what a worker said it committed."""

    environment_id: str
    repo: str
    branch: str
    commit: str
    declared_at: datetime


class IReadGitCommitDeclarationRepository(Protocol):
    """Read-only git-commit declaration queries."""

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclaration]:
        """The lease's explicit git-commit declarations, newest per ``(environment_id,
        repo)``, keyed the same way.

        Append-only, latest-wins. Keying on the environment as well as the repo keeps
        several environments from collapsing one env's branch onto another's."""
        ...

    def git_commit_declarations_for_leases(self, lease_ids: Sequence[str]) -> dict[str, list[DeclaredLeaseCommit]]:
        """:meth:`git_commit_declarations_for_lease` for every id in ``lease_ids``, with each
        declaration's instant, in grouped reads rather than one per lease
        (``bzh:bulk-reconstitution``, ``bzh:newest-per-key-read``). Each lease's list is ordered by
        ``(environment_id, repo)``; a lease that declared nothing is absent."""
        ...
