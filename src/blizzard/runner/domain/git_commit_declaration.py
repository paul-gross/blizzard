"""The worker git-commit declaration channel — ``blizzard runner artifact commit
--forge <f> --repo <r> --branch <b> --commit <sha>`` (issue #143, Phase 3).

Behind ``POST /api/leases/{lease_id}/git-commits``: a worker durably declares a
``git_commit``-kind artifact for a repo it touched, authorized by the lease token minted
at its own spawn (issue #113) — a structural sibling of
:class:`~blizzard.runner.domain.attachments.AttachmentService`.
:meth:`GitCommitDeclarationService.declare` is the one place the write happens
(``bzh:controller-read-only``).
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord

__all__ = [
    "GitCommitDeclarationRejected",
    "GitCommitDeclarationService",
    "GitCommitDeclarationUnknownRepo",
]

# The armed crash window (issue #113, mirrored for issue #143): the declaration row is
# durable — its single committed txn has returned — but the ``200`` has not. Recovery owes
# nothing but durability. Swept by
# ``tests/crash/test_kill9_sweep.py::test_kill9_at_declare_commit_crash_point``
# (``bzh:crash-point-registry``).
_CP_DECLARE_COMMIT_AFTER_RECORD = crashpoint(
    "declare-commit.after-record.before-response",
    "runner recorded the git-commit declaration durably but has not returned 200 — a kill -9 here must not lose it",
)


class GitCommitDeclarationRejected(Exception):
    """The presented lease token does not authorize this declare — the API edge maps
    this to ``403``."""


class GitCommitDeclarationUnknownRepo(Exception):
    """The declared ``(env, repo)`` is not in the lease's environments — the API edge
    maps this to ``400``.

    Deliberately an error at declare time rather than a drop later: the worker is alive,
    holds the context, and can re-run the verb correctly."""


class GitCommitDeclarationService:
    """Composition-root-wired: the write store, the workspace provider, and the clock.

    The provider is here because a declaration is only meaningful against the
    environment's repo manifest: it says which repos the lease authorizes, and it is the
    single source of the origin each one is later verified against."""

    def __init__(self, store: IWriteRunnerStore, clock: IClock, provider: IWorkspaceProvider) -> None:
        self._store = store
        self._clock = clock
        self._provider = provider

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
        """Record ``(env, repo, branch, commit)`` for ``lease`` and return the resolved
        environment id.

        Raises :class:`GitCommitDeclarationRejected` if ``presented_token`` does not
        authorize the lease, or :class:`GitCommitDeclarationUnknownRepo` if the env
        cannot be resolved or does not list ``repo``.

        ``lease`` is already resolved by the caller (``bzh:domain-takes-objects``) — this
        never looks a lease id up itself. Append-and-read-newest
        (``bzh:facts-not-status``): a repeat call for the same ``(lease, env, repo)`` is a
        correction, not an error. The environment is part of that key so a chunk holding
        several envs cannot have one env's declaration overwrite another's for the same
        repo."""
        stored_hash = self._store.lease_token_hash(lease.lease_id)
        if not check_lease_token(presented_token=presented_token, stored_hash=stored_hash):
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
        bound = [binding.environment_id for binding in self._store.bindings_for_chunk(lease.chunk_id)]
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
