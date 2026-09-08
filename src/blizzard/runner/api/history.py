"""``blizzard runner chunk history`` — a worker's read of its own chunk's transition
history (issue #237).

Lease-scoped and token-authorized, then forwarded to the hub as the runner principal.
``503`` unwired, ``404`` unknown/closed lease, ``403`` bad token, ``502`` on a failed
forward; authorization resolves before the hub is consulted. The merge into one
kind-discriminated timeline is this route's own projection (D3, D4, plan: hold wire/ to
its stated contract) — ``ChunkHistoryView`` stays a pydantic shape, never a projection."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Request

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.wire.chunk import BounceView, MigrationView, TransitionView
from blizzard.wire.history import ChunkHistoryView, HistoryRowView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/leases/{lease_id}/history", response_model=list[HistoryRowView])
def get_history(lease_id: str, request: Request) -> list[HistoryRowView]:
    """The worker's own chunk's timeline — transitions, migrations, and bounces merged
    oldest-first into one kind-discriminated read. Does not include the in-flight
    node-step this call is itself part of: a transition is recorded only once an
    attempt completes, so a worker must not read its own current step's absence as a
    gap in the history."""
    lease = authorized_lease(lease_id, request)
    upstream = HubProxy.of(request, "history").get(f"/api/fleet/chunks/{lease.chunk_id}", chunk_id=lease.chunk_id)
    return _rows(ChunkHistoryView.model_validate(upstream.json()))


@dataclass(frozen=True)
class _HistoryRow:
    """One fact of a chunk's history as a timeline row — a subclass per source history."""

    def view(self) -> HistoryRowView:
        raise NotImplementedError


@dataclass(frozen=True)
class _TransitionRow(_HistoryRow):
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


@dataclass(frozen=True)
class _MigrationRow(_HistoryRow):
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
            detail=m.source,
            recorded_at=m.recorded_at,
        )


@dataclass(frozen=True)
class _BounceRow(_HistoryRow):
    fact: BounceView

    def view(self) -> HistoryRowView:
        b = self.fact
        return HistoryRowView(kind="bounce", cause=b.cause, detail=b.envelope, recorded_at=b.recorded_at)


def _rows(detail: ChunkHistoryView) -> list[HistoryRowView]:
    """The chunk's transitions, migrations, and bounces merged into one kind-discriminated
    timeline, oldest-first by ``recorded_at``. Each input list already arrives oldest-first,
    so a stable sort on ``recorded_at`` alone preserves each kind's own order and only
    interleaves across kinds."""
    facts: list[_HistoryRow] = [
        *(_TransitionRow(t) for t in detail.history),
        *(_MigrationRow(m) for m in detail.migrations),
        *(_BounceRow(b) for b in detail.bounces),
    ]
    return sorted((f.view() for f in facts), key=lambda r: r.recorded_at)
