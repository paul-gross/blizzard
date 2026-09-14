"""The runner tick's slim batch status read (blizzard#521) — one bulk-by-id-set call to
each of the facts/route/decisions seams, in place of the nine per-chunk ``ChunkDetail``
round-trips the tick loop used to make."""

from __future__ import annotations

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.composition import HubServices
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView, ChunkUsageTotalView, PauseView


def chunk_statuses(chunk_ids: list[str], services: HubServices) -> list[ChunkStatusView]:
    """One :class:`ChunkStatusView` per requested id, de-duplicated preserving order. An
    id unknown to (or ephemeral in) the store is silently omitted, never a 404 — a lazy
    tick read routinely races a chunk's own deletion/grouping."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return []
    facts_by_id = services.chunks.facts.load_facts_for(ids)
    routes_by_id = services.chunks.route.routes_for(ids)
    decisions_by_id = services.chunks.decisions.live_decisions_for(ids)

    views: list[ChunkStatusView] = []
    for chunk_id in ids:
        facts = facts_by_id.get(chunk_id)
        if facts is None:
            continue
        route = routes_by_id.get(chunk_id)
        usage = facts.usage_total()
        pause = facts.open_pause()
        decision = decisions_by_id.get(chunk_id)
        views.append(
            ChunkStatusView(
                chunk_id=chunk_id,
                status=facts.status(),
                route_runner_id=route.runner_id if route is not None else None,
                pause=PauseView(by=pause.set_by, set_at=iso_utc(pause.set_at)) if pause is not None else None,
                latest_epoch=facts.latest_epoch(),
                restart_epochs=[r.epoch for r in facts.restarts],
                cost=ChunkUsageTotalView(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_create_tokens=usage.cache_create_tokens,
                    cost_usd=usage.cost_usd,
                    cost_partial=usage.cost_partial,
                ),
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
