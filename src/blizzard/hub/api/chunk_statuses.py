"""The runner tick's slim batch status read (blizzard#521) — one bulk-by-id-set call to
each of the facts/route/decisions seams, in place of the nine per-chunk ``ChunkDetail``
round-trips the tick loop used to make."""

from __future__ import annotations

from blizzard.hub.api.chunk_views import pause_view, usage_total_view
from blizzard.hub.composition import HubServices
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView


def chunk_statuses(chunk_ids: list[str], services: HubServices) -> list[ChunkStatusView]:
    """One :class:`ChunkStatusView` per requested id, de-duplicated preserving order. An
    id unknown to (or ephemeral in) the store is silently omitted, never a 404 — a lazy
    tick read routinely races a chunk's own deletion/grouping."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return []
    facts_by_id = services.chunks.facts.status_facts_for(ids)
    routes_by_id = services.chunks.route.routes_for(ids)
    decisions_by_id = services.chunks.decisions.live_decisions_for(ids)

    views: list[ChunkStatusView] = []
    for chunk_id in ids:
        facts = facts_by_id.get(chunk_id)
        if facts is None:
            continue
        route = routes_by_id.get(chunk_id)
        decision = decisions_by_id.get(chunk_id)
        views.append(
            ChunkStatusView(
                chunk_id=chunk_id,
                status=facts.status(),
                route_runner_id=route.runner_id if route is not None else None,
                pause=pause_view(facts.open_pause()),
                latest_epoch=facts.latest_epoch(),
                # Oldest first (issue #370) — mirrors `ChunkHistoryView.restarts`'s own
                # `(recorded_at, epoch)` order, the documented contract on the wire field.
                restart_epochs=[r.epoch for r in sorted(facts.restarts, key=lambda r: (r.recorded_at, r.epoch))],
                cost=usage_total_view(facts.usage_total()),
                decision=ChunkDecisionStatusView(
                    decision_id=decision.decision_id,
                    node_id=decision.node_id,
                    epoch=decision.epoch,
                    resolved_choice=decision.resolved_choice,
                    transitioned=decision.transitioned,
                )
                if decision is not None
                else None,
            )
        )
    return views
