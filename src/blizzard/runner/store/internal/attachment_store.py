"""SQLAlchemy adapter for the attachment repository seam (package-private, blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.attachments import IWriteAttachmentRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import attachments

_log = get_logger("blizzard.runner.store")


class AttachmentStore:
    """Read-write attachment adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def attachments_for_lease(self, lease_id: str) -> dict[str, str]:
        newest = (
            select(attachments.c.name, func.max(attachments.c.id).label("id"))
            .where(attachments.c.lease_id == lease_id)
            .group_by(attachments.c.name)
            .subquery()
        )
        stmt = select(attachments.c.name, attachments.c.content).join(newest, attachments.c.id == newest.c.id)
        return {str(r.name): str(r.content) for r in self._store.all(stmt)}

    def record_attachment(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        name: str,
        content: str,
        attached_at: datetime,
    ) -> None:
        # A single committed transaction — durable the instant this returns, so it
        # survives a `kill -9` right after (issue #113 Phase 2).
        with self._store.begin() as conn:
            conn.execute(
                attachments.insert().values(
                    lease_id=lease_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    name=name,
                    content=content,
                    attached_at=attached_at,
                )
            )
        _log.info("attachment recorded", lease_id=lease_id, name=name)


def _conforms_attachment_store(x: AttachmentStore) -> IWriteAttachmentRepository:
    return x
