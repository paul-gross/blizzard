"""``blizzard runner chunk history`` — a worker's read of its own chunk's transition
history.

Lease-scoped and token-authorized, then forwarded to the hub as the runner principal.
``503`` unwired, ``404`` unknown/closed lease, ``403`` bad token, ``502`` on a failed
forward; authorization resolves before the hub is consulted. The merge into one
kind-discriminated timeline is this route's own projection."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request

from blizzard.foundation.roles import dto
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.chunk import BounceView, MigrationView, RequeueView, ResolutionView, RestartView, TransitionView
from blizzard.wire.history import ChunkHistoryView, HistoryRowView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/history", response_model=list[HistoryRowView])
def get_history(lease_id: str, request: Request) -> list[HistoryRowView]:
    """The worker's own chunk's timeline — transitions, migrations, bounces, gate resolutions,
    requeues, and restarts merged
    oldest-first into one kind-discriminated read. Excludes the in-flight node-step."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "history").get(f"/api/fleet/chunks/{lease.chunk_id}", chunk_id=lease.chunk_id)
    return _rows(ChunkHistoryView.model_validate(upstream.json()))


@dto
@dataclass(frozen=True)
class _HistoryEntry:
    """One fact of a chunk's history as a timeline row — a subclass per source history."""

    def view(self) -> HistoryRowView:
        raise NotImplementedError


@dto
@dataclass(frozen=True)
class _TransitionEntry(_HistoryEntry):
    fact: TransitionView

    def view(self) -> HistoryRowView:
        t = self.fact
        return HistoryRowView(
            kind="transition",
            from_node=t.from_node_name or t.from_node_id,
            to_node=t.to_node_name or t.to_node_id,
            choice=t.choice_name,
            epoch=t.epoch,
            graph_name=t.graph_name,
            recorded_at=t.recorded_at,
        )


@dto
@dataclass(frozen=True)
class _MigrationEntry(_HistoryEntry):
    fact: MigrationView

    def view(self) -> HistoryRowView:
        m = self.fact
        from_label = "/".join(p for p in (m.from_graph_name, m.from_node_name or m.from_node_id) if p)
        to_label = "/".join(p for p in (m.to_graph_name, m.landed_node_name or m.landed_node_id) if p)
        return HistoryRowView(
            kind="migration",
            from_node=from_label or None,
            to_node=to_label or None,
            choice=m.choice_name,
            graph_name=m.to_graph_name,
            detail=None if m.source is None else str(m.source),
            recorded_at=m.recorded_at,
        )


@dto
@dataclass(frozen=True)
class _BounceEntry(_HistoryEntry):
    fact: BounceView

    def view(self) -> HistoryRowView:
        b = self.fact
        return HistoryRowView(kind="bounce", cause=b.cause, detail=b.envelope, recorded_at=b.recorded_at)


@dto
@dataclass(frozen=True)
class _ResolutionEntry(_HistoryEntry):
    fact: ResolutionView

    def view(self) -> HistoryRowView:
        r = self.fact
        return HistoryRowView(
            kind="resolution",
            from_node=r.node_name,
            choice=r.choice,
            epoch=r.epoch,
            by=r.resolved_by,
            note=r.note,
            recorded_at=r.resolved_at,
        )


@dto
@dataclass(frozen=True)
class _RequeueEntry(_HistoryEntry):
    fact: RequeueView

    def view(self) -> HistoryRowView:
        q = self.fact
        return HistoryRowView(kind="requeue", by=q.requeued_by, note=q.note, recorded_at=q.requeued_at)


@dto
@dataclass(frozen=True)
class _RestartEntry(_HistoryEntry):
    fact: RestartView

    def view(self) -> HistoryRowView:
        s = self.fact
        return HistoryRowView(
            kind="restart",
            from_node=s.from_node_name or s.from_node_id,
            to_node=s.to_node_name or s.to_node_id,
            epoch=s.epoch,
            graph_name=s.graph_name,
            by=s.restarted_by,
            note=s.note,
            recorded_at=s.recorded_at,
        )


def _rows(detail: ChunkHistoryView) -> list[HistoryRowView]:
    """The chunk's transitions, migrations, bounces, resolutions, requeues, and restarts merged
    into one kind-discriminated timeline, oldest-first by ``recorded_at``. Each input list already
    arrives oldest-first, so a stable sort on ``recorded_at`` alone preserves each kind's own order and only
    interleaves across kinds."""
    facts: list[_HistoryEntry] = [
        *(_TransitionEntry(t) for t in detail.history),
        *(_MigrationEntry(m) for m in detail.migrations),
        *(_BounceEntry(b) for b in detail.bounces),
        *(_ResolutionEntry(r) for r in detail.resolutions),
        *(_RequeueEntry(q) for q in detail.requeues),
        *(_RestartEntry(s) for s in detail.restarts),
    ]
    return sorted((f.view() for f in facts), key=lambda r: r.recorded_at)
