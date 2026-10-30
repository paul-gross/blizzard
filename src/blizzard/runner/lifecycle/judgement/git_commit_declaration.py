"""The worker git-commit declaration channel: a worker durably declares a
``git_commit``-kind artifact for a repo it touched, authorized by the lease token minted at its own
spawn, which the API edge checks. :meth:`GitCommitDeclarationService.declare` is the one place
the write happens (``bzh:controller-read-only``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.roles import domain_model
from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.environments.workspaces import IWorkspaceProviders
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository
from blizzard.runner.leases.git_commit_declaration import (
    GitCommitDeclaration,
    GitCommitDeclarationUnknownRepo,
    IReadGitCommitDeclarationRepository,
)
from blizzard.runner.leases.worker_lease import WorkerLease

__all__ = [
    "RIDES_NO_COMPLETION_NOTE",
    "DeclaredGitCommit",
    "GitCommitDeclarationService",
    "IWriteGitCommitDeclarationRepository",
    "resolve_declaring_environment",
]

#: What a declaration against a closed lease says back: it lands, but no completion carries it.
RIDES_NO_COMPLETION_NOTE = (
    "this lease is closed (an open takeover names it), so the declaration is recorded but rides no completion"
)


def resolve_declaring_environment(chunk_id: str, bound_env_ids: Sequence[str], named: str | None) -> str:
    """The env a declaration belongs to: the named one (checked against the chunk's bound
    environments), or the sole bound one when the worker named none.

    Inference stops where it stops being unambiguous: with several bound envs, guessing
    would silently attribute a branch to the wrong environment, so it is refused."""
    if named is not None:
        if named not in bound_env_ids:
            raise GitCommitDeclarationUnknownRepo(
                f"chunk {chunk_id} does not hold environment {named!r} — it holds {sorted(bound_env_ids)}"
            )
        return named
    if len(bound_env_ids) == 1:
        return bound_env_ids[0]
    if not bound_env_ids:
        raise GitCommitDeclarationUnknownRepo(f"chunk {chunk_id} holds no environment to declare against")
    raise GitCommitDeclarationUnknownRepo(
        f"chunk {chunk_id} holds {sorted(bound_env_ids)} — pass `--env` to say which one this commit is from"
    )


@domain_model
@dataclass(frozen=True)
class DeclaredGitCommit:
    """What a landed declaration reports back: the environment it resolved to, and a note
    when it rides no completion."""

    environment_id: str
    note: str | None = None


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
        ``environment_id``, a single committed transaction so it survives a
        ``kill -9`` before the collection reads it. Append-only: a later call for the
        same key is a correction, read back as the replacement, never merged."""
        ...


# Armed window: the declaration row is durable but the ``200`` has not returned; recovery owes nothing
# but durability. Swept by tests/crash/test_kill9_sweep.py (``bzh:crash-point-registry``).
_CP_DECLARE_COMMIT_AFTER_RECORD = crashpoint(
    "declare-commit.after-record.before-response",
    "runner recorded the git-commit declaration durably but has not returned 200 — a kill -9 here must not lose it",
)


class GitCommitDeclarationService:
    """Composition-root-wired: the write store, the environment store, the host's workspaces, the
    outbound buffer (whether the lease's outcome is still unbuffered), and the clock. The repo manifest of the
    provider of the workspace the environment's binding names says which repos the lease authorizes and is the one
    source of each origin later verified."""

    def __init__(
        self,
        store: IWriteGitCommitDeclarationRepository,
        clock: IClock,
        workspaces: IWorkspaceProviders,
        *,
        environments: IReadEnvironmentRepository,
        outbound: IReadOutboundRepository,
    ) -> None:
        self._store = store
        self._clock = clock
        self._workspaces = workspaces
        self._environments = environments
        self._outbound = outbound

    def declare(
        self,
        worker: WorkerLease,
        *,
        repo: str,
        branch: str,
        commit: str,
        environment_id: str | None = None,
    ) -> DeclaredGitCommit:
        """Record ``(env, repo, branch, commit)`` for ``worker``'s lease and report the resolved
        environment. ``worker`` is already resolved and its token checked by the caller
        (``bzh:domain-takes-objects``). Raises :class:`GitCommitDeclarationUnknownRepo` when the
        env is unresolvable or does not list ``repo``, :class:`GitCommitDeclarationOnClosedLease`
        when the lease's standing refuses the verb, or :class:`GitCommitDeclarationTooLate` once
        the lease's outcome is buffered.
        Append-and-read-newest (``bzh:facts-not-status``): a repeat call for the same ``(lease, env,
        repo)`` is a correction. The env is part of that key, so one env cannot overwrite another."""
        lease = worker.lease
        bindings = self._environments.bindings_for_chunk(lease.chunk_id)
        resolved_env = resolve_declaring_environment(
            lease.chunk_id, [binding.environment_id for binding in bindings], environment_id
        )
        workspace_id = next((b.workspace_id for b in bindings if b.environment_id == resolved_env), None)
        provider = self._workspaces.provider(workspace_id)
        record = GitCommitDeclaration(resolved_env, repo, branch, commit).declared_by(
            worker,
            known_repos=[repo.name for repo in provider.repos(resolved_env)] if provider is not None else [],
            outcome_pending=lease.lease_id in self._outbound.pending_submission_lease_ids(),
            at=self._clock.now(),
        )
        self._store.record_git_commit_declaration(
            lease_id=record.lease_id,
            chunk_id=record.chunk_id,
            node_id=record.node_id,
            epoch=record.epoch,
            environment_id=record.environment_id,
            repo=record.repo,
            branch=record.branch,
            commit=record.commit,
            declared_at=record.declared_at,
        )
        _CP_DECLARE_COMMIT_AFTER_RECORD.reached()
        return DeclaredGitCommit(
            environment_id=resolved_env, note=None if record.rides_completion else RIDES_NO_COMPLETION_NOTE
        )
