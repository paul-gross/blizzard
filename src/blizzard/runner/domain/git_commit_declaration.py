"""The worker git-commit declaration channel (issue #143): a worker durably declares a
``git_commit``-kind artifact for a repo it touched, authorized by the lease token minted at its own
spawn (issue #113). :meth:`GitCommitDeclarationService.declare` is the one place the write happens
(``bzh:controller-read-only``)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.auth.tokens import IReadTokenRepository
from blizzard.runner.domain.lease_auth import LeaseToken
from blizzard.runner.domain.leases import LeaseRecord
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.environments.repository import IReadEnvironmentRepository

__all__ = [
    "GitCommitDeclarationRecord",
    "GitCommitDeclarationRejected",
    "GitCommitDeclarationService",
    "GitCommitDeclarationUnknownRepo",
    "IReadGitCommitDeclarationRepository",
    "IWriteGitCommitDeclarationRepository",
]


@dataclass(frozen=True)
class GitCommitDeclarationRecord:
    """A worker's explicit git-commit declaration for one repo in one environment.

    Carries no forge: the origin it is verified against is read from the environment's
    repo manifest. ``environment_id`` is part of the identity, never a decoration."""

    environment_id: str
    repo: str
    branch: str
    commit: str


class IReadGitCommitDeclarationRepository(Protocol):
    """Read-only git-commit declaration queries (held by read-path edges)."""

    def git_commit_declarations_for_lease(self, lease_id: str) -> dict[tuple[str, str], GitCommitDeclarationRecord]:
        """The lease's explicit git-commit declarations, newest per ``(environment_id,
        repo)`` (issue #143, Phase 3), keyed the same way.

        Append-only, latest-wins. Keying on the environment as well as the repo keeps
        several environments from collapsing one env's branch onto another's."""
        ...


class IWriteGitCommitDeclarationRepository(IReadGitCommitDeclarationRepository, Protocol):
    """Read-write git-commit declaration store — held only by the domain."""

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
        """Append a worker's explicit git-commit declaration for ``repo`` in
        ``environment_id`` (issue #143), a single committed transaction so it survives a
        ``kill -9`` before the collection reads it. Append-only: a later call for the
        same key is a correction, read back as the replacement, never merged."""
        ...


# Armed window: the declaration row is durable but the ``200`` has not returned; recovery owes nothing
# but durability. Swept by tests/crash/test_kill9_sweep.py (``bzh:crash-point-registry``).
_CP_DECLARE_COMMIT_AFTER_RECORD = crashpoint(
    "declare-commit.after-record.before-response",
    "runner recorded the git-commit declaration durably but has not returned 200 — a kill -9 here must not lose it",
)


class GitCommitDeclarationRejected(Exception):
    """The presented lease token does not authorize this declare — the API edge maps
    this to ``403``."""


class GitCommitDeclarationUnknownRepo(Exception):
    """The declared ``(env, repo)`` is not in the lease's environments — mapped to ``400``. An error at
    declare time rather than a drop later: the worker is alive and can re-run the verb correctly."""


class GitCommitDeclarationService:
    """Composition-root-wired: the write store, the token store (for authorization), the
    environment store (for bound-environment resolution), the workspace provider, and the
    clock. The provider is here because the environment's repo manifest says which repos
    the lease authorizes, and is the one source of the origin each is later verified against."""

    def __init__(
        self,
        store: IWriteGitCommitDeclarationRepository,
        clock: IClock,
        provider: IWorkspaceProvider,
        *,
        tokens: IReadTokenRepository,
        environments: IReadEnvironmentRepository,
    ) -> None:
        self._store = store
        self._clock = clock
        self._provider = provider
        self._tokens = tokens
        self._environments = environments

    def declare(
        self,
        lease: LeaseRecord,
        *,
        presented_token: str | None,
        repo: str,
        branch: str,
        commit: str,
        environment_id: str | None = None,
    ) -> str:
        """Record ``(env, repo, branch, commit)`` for ``lease`` and return the resolved environment id.
        Raises :class:`GitCommitDeclarationRejected` on an unauthorizing token, or
        :class:`GitCommitDeclarationUnknownRepo` when the env is unresolvable or does not list ``repo``.
        Append-and-read-newest (``bzh:facts-not-status``): a repeat call for the same ``(lease, env,
        repo)`` is a correction. The env is part of that key, so one env cannot overwrite another."""
        stored_hash = self._tokens.lease_token_hash(lease.lease_id)
        if not LeaseToken(presented_token, stored_hash).valid:
            raise GitCommitDeclarationRejected(f"presented token does not authorize lease {lease.lease_id}")
        resolved_env = self._resolve_environment(lease, environment_id)
        known = [binding.name for binding in self._provider.repos(resolved_env)]
        if repo not in known:
            raise GitCommitDeclarationUnknownRepo(
                f"environment {resolved_env!r} has no repo {repo!r} — it holds {sorted(known)}"
            )
        self._store.record_git_commit_declaration(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            environment_id=resolved_env,
            repo=repo,
            branch=branch,
            commit=commit,
            declared_at=self._clock.now(),
        )
        _CP_DECLARE_COMMIT_AFTER_RECORD.reached()
        return resolved_env

    def _resolve_environment(self, lease: LeaseRecord, environment_id: str | None) -> str:
        """The env this declaration belongs to: the named one (checked against the
        chunk's bindings), or the sole bound one when the worker named none.

        Inference stops where it stops being unambiguous: with several bound envs, guessing
        would silently attribute a branch to the wrong environment, so it is refused."""
        bound = [binding.environment_id for binding in self._environments.bindings_for_chunk(lease.chunk_id)]
        if environment_id is not None:
            if environment_id not in bound:
                raise GitCommitDeclarationUnknownRepo(
                    f"chunk {lease.chunk_id} does not hold environment {environment_id!r} — it holds {sorted(bound)}"
                )
            return environment_id
        if len(bound) == 1:
            return bound[0]
        if not bound:
            raise GitCommitDeclarationUnknownRepo(f"chunk {lease.chunk_id} holds no environment to declare against")
        raise GitCommitDeclarationUnknownRepo(
            f"chunk {lease.chunk_id} holds {sorted(bound)} — pass `--env` to say which one this commit is from"
        )
