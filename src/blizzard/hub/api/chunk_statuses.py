"""The runner tick's slim batch status read — one bulk-by-id-set call to
each of the facts/route/decisions seams, and to the chunks' projects. The ``decision`` reported
is the current visit's (:meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.current_visit_decision`),
not the chunk-wide newest."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.api.chunk_views import pause_view, usage_total_view
from blizzard.hub.api.project_label_fields import project_label_fields
from blizzard.hub.domain.chunk.ports.decisions import IReadChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.wire.chunk import ChunkDecisionStatusView, ChunkStatusView


@dataclass(frozen=True)
class ChunkStatusReads:
    """The tenant reads one batch status read takes: the chunks' facts, routes, decisions, records and projects."""

    facts: IReadChunkFactsRepository
    route: IReadChunkRouteRepository
    decisions: IReadChunkDecisionsRepository
    record: IReadChunkRecordRepository
    projects: IReadProjectRepository


def chunk_statuses(chunk_ids: list[str], reads: ChunkStatusReads) -> list[ChunkStatusView]:
    """One :class:`ChunkStatusView` per requested id, de-duplicated preserving order. An
    id unknown to (or ephemeral in) the store is silently omitted, never a 404 — a lazy
    tick read routinely races a chunk's own deletion/grouping."""
    ids = list(dict.fromkeys(chunk_ids))
    if not ids:
        return []
    facts_by_id = reads.facts.status_facts_for(ids)
    routes_by_id = reads.route.routes_for(ids)
    visit_decisions = {
        chunk_id: decision
        for chunk_id, facts in facts_by_id.items()
        if (decision := facts.current_visit_decision()) is not None
    }
    resolved_choices = reads.decisions.resolved_choices(d.decision_id for d in visit_decisions.values())
    project_by_id = {chunk_id: chunk.project_id for chunk_id, chunk in reads.record.get_many(ids).items()}
    projects = reads.projects.get_many(sorted({p for p in project_by_id.values() if p is not None}))

    views: list[ChunkStatusView] = []
    for chunk_id in ids:
        facts = facts_by_id.get(chunk_id)
        if facts is None:
            continue
        route = routes_by_id.get(chunk_id)
        decision = visit_decisions.get(chunk_id)
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
                    resolved_choice=resolved_choices.get(decision.decision_id),
                    transitioned=False,
                )
                if decision is not None
                else None,
                **project_label_fields(project_id, project),
            )
        )
    return views
