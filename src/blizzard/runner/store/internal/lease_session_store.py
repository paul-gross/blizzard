"""SQLAlchemy adapter for the lease-session repository seam (package-private,
blizzard#411 Phase 4).

Session-pool head, session-identity lookups, session-end, and preamble facts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.leases import IWriteLeaseSessionRepository, LeaseRecord, PoolHead
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.store.internal.base import RunnerStoreConnections, lease_select, row_to_lease
from blizzard.runner.store.schema import (
    lease_context,
    lease_spawns,
    leases,
    session_ends,
    session_preamble_facts,
    usage_facts,
)

_log = get_logger("blizzard.runner.store")


class LeaseSessionStore:
    """Read-write session adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    # --- reads --------------------------------------------------------------

    def latest_session_id(self, chunk_id: str, node_name: str | None) -> str | None:
        stmt = lease_select().where(leases.c.chunk_id == chunk_id).where(leases.c.session_id.is_not(None))
        if node_name is not None:
            stmt = stmt.where(lease_context.c.node_name == node_name)
        stmt = stmt.order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        rows = self._store.all(stmt)
        return str(rows[0].session_id) if rows else None

    def pool_head(self, chunk_id: str, session_name: str) -> PoolHead | None:
        """The newest session-bearing lease stamping ``session_name`` — the pool's head.

        Same ordering and same session-bearing filter as :meth:`latest_session_id`,
        keyed on the stamped pool name rather than the node name."""
        stmt = (
            lease_select()
            .where(leases.c.chunk_id == chunk_id)
            .where(leases.c.session_id.is_not(None))
            .where(lease_context.c.session_name == session_name)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
        rows = self._store.all(stmt)
        if not rows:
            return None
        row = rows[0]
        return PoolHead(
            session_id=str(row.session_id),
            lease_id=str(row.lease_id),
            resolved_model=row.resolved_model,
            resolved_effort=row.resolved_effort,
        )

    def session_invocation_count(self, session_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(usage_facts)
            .join(leases, leases.c.lease_id == usage_facts.c.lease_id)
            .where(leases.c.session_id == session_id)
        )
        rows = self._store.all(stmt)
        return int(rows[0][0]) if rows else 0

    def lease_for_session(self, session_id: str) -> LeaseRecord | None:
        """The newest lease that ran ``session_id`` — same ordering as `pool_head`."""
        stmt = (
            lease_select()
            .where(leases.c.session_id == session_id)
            .order_by(leases.c.created_at.desc(), leases.c.lease_id.desc())
        )
        rows = self._store.all(stmt)
        return row_to_lease(rows[0]) if rows else None

    def session_ended_lease_ids(self) -> set[str]:
        newest_spawn = (
            select(lease_spawns.c.lease_id, func.max(lease_spawns.c.spawned_at).label("spawned_at"))
            .group_by(lease_spawns.c.lease_id)
            .subquery()
        )
        stmt = (
            select(session_ends.c.lease_id)
            .select_from(session_ends.outerjoin(newest_spawn, newest_spawn.c.lease_id == session_ends.c.lease_id))
            # No spawn fact: fall back to the unscoped reading, which over-reports
            # "declared done" and so can only suppress a resume, never invent one.
            .where(or_(newest_spawn.c.spawned_at.is_(None), session_ends.c.ended_at >= newest_spawn.c.spawned_at))
            .distinct()
        )
        return {str(r.lease_id) for r in self._store.all(stmt)}

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        # Ordered on the autoincrement pk, not on `recorded_at` or implicit insert order
        # (`bzh:sql-portable`).
        rows = self._store.all(
            select(session_preamble_facts.c.blizzard_digest, session_preamble_facts.c.workspace_digest)
            .where(session_preamble_facts.c.session_id == session_id)
            .order_by(session_preamble_facts.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        return PreambleFingerprint(blizzard=str(rows[0].blizzard_digest), workspace=str(rows[0].workspace_digest))

    # --- writes -------------------------------------------------------------

    def record_session_end(self, *, lease_id: str, ended_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(session_ends.insert().values(lease_id=lease_id, ended_at=ended_at))
        _log.info("session end recorded", lease_id=lease_id)

    def record_session_preamble(self, session_id: str, *, fingerprint: PreambleFingerprint, at: datetime) -> None:
        # A plain append, no check-then-insert: a per-spawn fact whose newest row is the
        # answer, not a once-per-key guard.
        with self._store.begin() as conn:
            conn.execute(
                session_preamble_facts.insert().values(
                    session_id=session_id,
                    blizzard_digest=fingerprint.blizzard,
                    workspace_digest=fingerprint.workspace,
                    recorded_at=at,
                )
            )
        _log.info("session preamble recorded", session_id=session_id)


def _conforms_lease_session_store(x: LeaseSessionStore) -> IWriteLeaseSessionRepository:
    return x
