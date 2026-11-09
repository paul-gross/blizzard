"""Seed a lease's attachments and git-commit declarations the way production admits them — each inside a real
lease-locked unit, since both write methods require one."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from blizzard.runner.leases.lease_lock import IWriteLeaseLock
from blizzard.runner.leases.operator_requests import IWriteAttachmentRepository
from blizzard.runner.lifecycle.judgement.git_commit_declaration import IWriteGitCommitDeclarationRepository


def _locker(store: object, lock: IWriteLeaseLock | None) -> IWriteLeaseLock:
    """The lock seam: the explicit one, else the store itself — the test facade is both."""
    return lock if lock is not None else cast(IWriteLeaseLock, store)


def seed_attachment(
    store: IWriteAttachmentRepository,
    *,
    lock: IWriteLeaseLock | None = None,
    lease_id: str,
    chunk_id: str,
    node_id: str,
    epoch: int,
    name: str,
    content: str,
    attached_at: datetime,
) -> None:
    """Record one attachment on a freshly locked unit for ``lease_id``."""
    with _locker(store, lock).locked(lease_id) as unit:
        store.record_attachment(
            lease_id=lease_id,
            chunk_id=chunk_id,
            node_id=node_id,
            epoch=epoch,
            name=name,
            content=content,
            attached_at=attached_at,
            unit=unit,
        )


def seed_git_commit_declaration(
    store: IWriteGitCommitDeclarationRepository,
    *,
    lock: IWriteLeaseLock | None = None,
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
    """Record one git-commit declaration on a freshly locked unit for ``lease_id``."""
    with _locker(store, lock).locked(lease_id) as unit:
        store.record_git_commit_declaration(
            lease_id=lease_id,
            chunk_id=chunk_id,
            node_id=node_id,
            epoch=epoch,
            environment_id=environment_id,
            repo=repo,
            branch=branch,
            commit=commit,
            declared_at=declared_at,
            unit=unit,
        )
