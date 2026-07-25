"""The worker git-commit declaration channel — ``blizzard runner artifact commit
--forge <f> --repo <r> --branch <b> --commit <sha>`` (issue #143, Phase 3).

Behind ``POST /api/leases/{lease_id}/git-commits``: a worker durably declares a
``git_commit``-kind artifact for a repo it touched, authorized by the lease token minted
at its own spawn (issue #113, Phase 1) — a structural sibling of
:class:`~blizzard.runner.domain.attachments.AttachmentService`.
:meth:`GitCommitDeclarationService.declare` is the one place the write happens
(``bzh:controller-read-only`` — the API edge resolves the lease to an object and
delegates here rather than writing through a store it holds itself).

Nothing yet reads a declaration back — that is Phase 4's ADVANCE rewrite, which will
verify each declared ``(forge, repo, branch, commit)`` read-only against its forge and
collect it as a submitted artifact. This phase only makes the declaration durable,
single-transaction (``runner/store/internal/sqlalchemy_store.py``'s
``record_git_commit_declaration``) so it survives a ``kill -9`` between the declare and
whatever later collection would otherwise read it back.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.runner.domain.lease_auth import check_lease_token
from blizzard.runner.store.repository import IWriteRunnerStore, LeaseRecord

__all__ = ["GitCommitDeclarationRejected", "GitCommitDeclarationService"]

# The dangerous window criterion 3 names (issue #113, mirrored for issue #143): the
# declaration row is durable — its single committed txn (``record_git_commit_declaration``)
# has returned — but the ``200`` has not, so a ``kill -9`` here is exactly "a runner dies
# between the declare and whatever collection would read it back". Recovery owes nothing
# but durability: the row is on disk, and a later collection (Phase 4) / the recovering
# ADVANCE tick re-derives it via ``git_commit_declarations_for_lease``. Swept by
# ``tests/crash/test_kill9_sweep.py::test_kill9_at_declare_commit_crash_point``
# (``bzh:crash-point-registry``); unarmed, ``reached()`` is one module-global compare.
_CP_DECLARE_COMMIT_AFTER_RECORD = crashpoint(
    "declare-commit.after-record.before-response",
    "runner recorded the git-commit declaration durably but has not returned 200 — a kill -9 here must not lose it",
)


class GitCommitDeclarationRejected(Exception):
    """The presented lease token does not authorize this declare — the API edge maps
    this to ``403``."""


class GitCommitDeclarationService:
    """Composition-root-wired: the write store and clock (issue #143, Phase 3)."""

    def __init__(self, store: IWriteRunnerStore, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def declare(
        self, lease: LeaseRecord, *, presented_token: str | None, forge: str, repo: str, branch: str, commit: str
    ) -> None:
        """Record ``(forge, repo, branch, commit)`` for ``lease``, or raise
        :class:`GitCommitDeclarationRejected` if ``presented_token`` does not authorize it.

        ``lease`` is already resolved by the caller (``bzh:domain-takes-objects``) —
        this never looks a lease id up itself. Append-and-read-newest
        (``bzh:facts-not-status``): a repeat call for the same ``(lease, repo)`` is a
        correction, not an error."""
        stored_hash = self._store.lease_token_hash(lease.lease_id)
        if not check_lease_token(presented_token=presented_token, stored_hash=stored_hash):
            raise GitCommitDeclarationRejected(f"presented token does not authorize lease {lease.lease_id}")
        self._store.record_git_commit_declaration(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            node_id=lease.node_id,
            epoch=lease.epoch,
            forge=forge,
            repo=repo,
            branch=branch,
            commit=commit,
            declared_at=self._clock.now(),
        )
        # The row is durable (the txn above committed) but the caller has not yet returned
        # the 200 — criterion 3's kill-9 window (armed only under the crash sweep).
        _CP_DECLARE_COMMIT_AFTER_RECORD.reached()
