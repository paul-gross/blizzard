"""The runner tick's slim batch status read — one bulk-by-id-set call to
each of the facts/route/decisions seams, and to the chunks' projects."""

from __future__ import annotations

from blizzard.hub.api.chunk_views import pause_view, usage_total_view
from blizzard.hub.api.project_label_fields import project_label_fields
from blizzard.hub.composition import TenantServices
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView


def chunk_statuses(chunk_ids: list[str], services: TenantServices) -> list[ChunkStatusView]:
    """One :class:`ChunkStatusView` per requested id, de-duplicated preserving order. An
    id unknown to (or ephemeral in) the store is silently omitted, never a 404 — a lazy
    tick read routinely races a chunk's own deletion/grouping."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return []
    facts_by_id = services.chunks.facts.status_facts_for(ids)
    routes_by_id = services.chunks.route.routes_for(ids)
    decisions_by_id = services.chunks.decisions.live_decisions_for(ids)
    project_by_id = {chunk_id: chunk.project_id for chunk_id, chunk in services.chunks.record.get_many(ids).items()}
    projects = services.projects.get_many(sorted({p for p in project_by_id.values() if p is not None}))

    views: list[ChunkStatusView] = []
    for chunk_id in ids:
        facts = facts_by_id.get(chunk_id)
        if facts is None:
            continue
        route = routes_by_id.get(chunk_id)
        decision = decisions_by_id.get(chunk_id)
        project_id = project_by_id.get(chunk_id)
        project = projects.get(project_id) if project_id is not None else None
        views.append(
            ChunkStatusView(
                chunk_id=chunk_id,
                status=facts.status(),
                route_runner_id=route.runner_id if route is not None else None,
                pause=pause_view(facts.open_pause()),
                latest_epoch=facts.latest_epoch(),
                # Oldest first, as `ChunkHistoryView.restarts` — the documented contract on the wire field.
                restart_epochs=[r.epoch for r in facts.restart_history()],
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
                **project_label_fields(project_id, project),
            )
        )
    return views
